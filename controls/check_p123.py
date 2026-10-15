#!/usr/bin/env python3
"""P123 gates G2-G6 (protocols/ACTINV-P123_PROTOCOL.md): radiological completeness.

  G2  Unchanged by default. The unmodified gate spec (examples/fns_fe_5min.json), every example spec that sets a
      `photon` or `radiological` section and is accepted by both binaries (listed in the record; there are none in
      examples/ at the time of writing), and two specs this checker writes (FNS Fe with the installed photon response;
      FNS Fe with a legacy radiological table) give byte-identical results from the reference and candidate binaries
      once the wall-clock `ms` field is masked. The photon response builder's default output (offline, from the local
      NIST page cache) is byte-identical between the reference commit's script and the working tree's, `--elements
      all` reproduces the installed all-element response, and the default Fe build matches the installed Fe response
      in every numeric curve (its provenance records the hash of a NIST page that has since changed).
  G3  Point-source dose (manual Eq. 63), recomputed from the emitted per-group photon source and the photon
      response's own curves (log-log interpolation) for every step: FNS Fe at 1 m and 3 m with air attenuation (the
      response is rebuilt with `--air-attenuation`), with the installed response (no air curve: the dose must be
      unavailable, and with `air_attenuation: false` it must be computed), and with the Steiner 22-group structure.
      Tolerance 1e-9 relative. The 1 m / 3 m ratio is checked per group against 9 exp(2 mu) with mu in 1/m. The
      Steiner grouping of the line spectra is recomputed from this script's own MF=8/MT=457 parse.
  G4  Bremsstrahlung (manual Eqs. 65-68), recomputed from this script's own MT=457 parse (mean beta energy of the
      STYP=1 spectrum) and the atom-fraction mean Z: group photons and energies, the empty 0 eV-edge group and its
      reported energy, the totals, the missing-input and no-activity lists, and the point-source dose of the
      component. The decay-photon fields are identical with and without the option. A two-element material checks
      that Z_eff is an atom-fraction mean.
  G5  Bundled tables (needs the Part B files; "not merged" status otherwise): converters re-run on the pinned sources
      reproduce the committed tables byte for byte; at least 25 rows per table (and every row, as an extra) match
      values read from the source XML/XHTML by this script's own parser; report-only agreement with FISPACT-II
      a2_2012 and clear_2012; the ICRP 119 script run locally reproduces at least 98 % of FISPACT hazards_2012
      ICRP-72 rows within 2 %, with every exception listed.
  G6  A2 index. A table written by this checker (kind `a2`) is evaluated by the candidate on FNS Fe, and so is the
      bundled `us-49cfr173-a2` table (when merged); the index is recomputed as sum(activity / A2) from the step
      activities and the table, within 1e-12 relative. Nuclides without an A2 value are listed.

Overall PASS iff G2-G6 all pass. A gate that could not run because the Part B files are absent has pass = null and
status "not_merged"; the verdict is then INCOMPLETE (exit 2). FAIL exits 1, PASS exits 0. Runs are serial, without a
shell, each under a timeout. Only the candidate runs the variants that use P123 options (the reference predates them).

Usage:
  check_p123.py --reference REF_BIN --candidate CAND_BIN
      [--workdir target/p123-work] [--out results/p123_radiological.json] [--timeout 3600] [--reuse]
      [--gates G2,G3,G4,G5,G6] [--nist-cache ~/.cache/nist-html]
      [--photon-response ~/nuclear-data/photon-response/nist-xcom-air-fe.json]
      [--regulatory-sources ~/nuclear-data/regulatory]
      [--fispact-dir ~/nuclear-data/regulatory/fispact_decay_2012_extracted]

Run inside the required systemd cgroup. G5 alone needs no binary.
"""
import argparse
import bisect
import copy
import datetime
import decimal
import hashlib
import html
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P123_PROTOCOL.md"
BASE_COMMIT = "2f3daa4"  # protocol: base commit, the reference build
DATA_DIR = ROOT / "crates" / "actinv-core" / "data" / "regulatory"
SCRIPTS_DIR = ROOT / "scripts" / "regulatory"
BUILDER = ROOT / "scripts" / "build_photon_response.py"

HOME = Path.home()
DEFAULT_PHOTON_RESPONSE = HOME / "nuclear-data" / "photon-response" / "nist-xcom-air-fe.json"
DEFAULT_PHOTON_RESPONSE_ALL = HOME / "nuclear-data" / "photon-response" / "nist-xcom-all.json"
DEFAULT_NIST_CACHE = HOME / ".cache" / "nist-html"
DEFAULT_SOURCES = HOME / "nuclear-data" / "regulatory"
DEFAULT_FISPACT = DEFAULT_SOURCES / "fispact_decay_2012_extracted"

# Physical and model constants, restated here rather than imported.
EV_J = 1.602176634e-19
AIR_DENSITY_DEFAULT = 1.20479e-3  # g/cm3, NIST dry air near sea level (docs/guide/specification.md)
SUBTHRESHOLD_TOL = 1e-4  # photon.rs SUBTHRESHOLD_POWER_TOLERANCE
UNGROUPED_TOL = 1e-9  # photon.rs point_source_dose: ungrouped power share that withholds the dose
BREMS_A_PER_KEV = 2.76e-6  # manual Eq. (65), "a"
BREMS_MODEL = "fispact-ii-c74-jarvis-mean-beta-energy"
STEINER_22_MEV = [0.0, 0.01, 0.10, 0.20, 0.40, 1.00, 1.50] + [2.0 + 0.5 * k for k in range(13)] + [10.0, 12.0, 14.0]
POINT_MASS_G = 2.5  # a non-unit mass, so a missing mass factor cannot hide
MIN_BREMS_ACTIVITY_BQ_G = 1e-3  # Bq/g: dynamic bremsstrahlung nuclides must exceed this at some step

REL_TOL = 1e-9
ABS_TOL_ZERO = 1e-30
A2_REL_TOL = 1e-12
MAX_LISTED = 20

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
Z_OF = {symbol: index + 1 for index, symbol in enumerate(SYMBOLS)}
# Standard atomic weights (IUPAC, abridged) for the two-element Z_eff cross-check only.
ATOMIC_WEIGHT = {"Fe": 55.845, "Cr": 51.9961}

decimal.getcontext().prec = 60


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


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


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


class Acc:
    """Accumulates comparisons of one quantity: count, worst error, and a capped list of mismatches."""

    def __init__(self, rel_tol=REL_TOL):
        self.rel_tol = rel_tol
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
        elif isinstance(exp, (bool, str, list, dict)) or isinstance(got, (bool, str, list, dict)):
            ok = got == exp
            err = 0.0 if ok else math.inf
        elif exp == 0.0:
            err = abs(got)
            ok = err <= ABS_TOL_ZERO
            self.worst_abs_where_zero = max(self.worst_abs_where_zero, err)
        else:
            err = abs(got - exp) / abs(exp)
            ok = err <= self.rel_tol
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
    def __init__(self, rel_tol=REL_TOL):
        self.rel_tol = rel_tol
        self.accs = {}

    def check(self, quantity, where, got, exp):
        return self.accs.setdefault(quantity, Acc(self.rel_tol)).check(where, got, exp)

    def n_bad(self):
        return sum(a.n_bad for a in self.accs.values())

    def n(self):
        return sum(a.n for a in self.accs.values())

    def report(self):
        return {k: v.report() for k, v in sorted(self.accs.items())}

    def summary(self):
        return {"n_comparisons": self.n(), "n_mismatches": self.n_bad(), "tolerance_relative": self.rel_tol,
                "quantities": self.report()}


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


def skip_tab1(lines, index):
    """Index after the TAB1 record at `index`: NR interpolation pairs then NP points, six numbers per line."""
    head = cont(lines[index])
    nr, npts = head[4], head[5]
    return index + 1 + -(-2 * nr // 6) + -(-2 * npts // 6)


def parse_decay_section(lines):
    """One MF=8/MT=457 section: half-life, mean energies, and per spectrum STYP, LCON, FD, ER_AV and the discrete
    lines (energy, intensity). Continuous parts are skipped but flagged."""
    za, awr, _lis, liso, nst, nsp = cont(lines[0])
    if not (math.isfinite(za) and za > 0 and math.isfinite(awr) and awr > 0):
        raise ValueError(f"invalid decay HEAD ZA/AWR {za}/{awr}")
    energy_head, energies, nxt = read_list(lines, 1)
    mode_head, _mode_values, nxt = read_list(lines, nxt)
    spectra = []
    for _ in range(nsp):
        head, values, nxt = read_list(lines, nxt)
        styp, lcon, lcov, ner = int(round(head[1])), head[2], head[3], head[5]
        discrete = []
        if lcon != 1:
            for _k in range(ner):
                dhead, dvalues, nxt = read_list(lines, nxt)
                discrete.append((dhead[0], dvalues[2] if len(dvalues) > 2 else 0.0))
        if lcon != 0:
            nxt = skip_tab1(lines, nxt)
        if lcov in (1, 3) and lcon != 0:
            nxt = read_list(lines, nxt)[2]
        if lcov in (2, 3):
            nxt = read_list(lines, nxt)[2]
        spectra.append({"styp": styp, "lcon": lcon, "fd": values[0] if values else 0.0,
                        "average_energy": values[2] if len(values) > 2 else 0.0, "discrete": discrete,
                        "has_continuum": lcon != 0})
    if nxt != len(lines):
        raise ValueError(f"MF=8/MT=457 section has {len(lines) - nxt} unconsumed record line(s)")
    return {"za": int(round(za)), "liso": liso, "awr": awr, "nst": nst, "half_life": energy_head[0],
            "energies": energies, "spectra": spectra}


def scan_decay_file(path, needed):
    """Stream an ENDF-6 decay sublibrary and parse the `needed` (ZA, LISO) keys. Returns (records, all keys)."""
    records, all_keys = {}, set()
    cur = None
    buf = []

    def flush():
        if cur is None or not buf:
            return
        head = cont(buf[0])
        key = (int(round(head[0])), head[3])
        all_keys.add(key)
        if key in needed:
            records[key] = parse_decay_section(buf)

    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            if line[70:75] == " 8457":
                mat = line[66:70]
                if cur != mat:
                    cur = mat
                    buf = []
                buf.append(line)
            elif cur is not None and buf:
                flush()
                cur, buf = None, []
        flush()
    return records, all_keys


def load_decay(primary, fallback, needed):
    """The primary file wins; the fallback supplies only keys absent from the primary (run.rs merge)."""
    records, p_keys = scan_decay_file(primary, needed)
    remaining = needed - set(records) - p_keys
    if fallback and remaining:
        f_records, _ = scan_decay_file(fallback, remaining)
        records.update(f_records)
    return records


NUCLIDE_NAME = re.compile(r"^([A-Z][a-z]?)(\d+)(?:m(\d+))?$")


def key_of(name):
    """'Mn58m1' -> (ZA, LISO)."""
    m = NUCLIDE_NAME.match(name)
    if not m or m.group(1) not in Z_OF:
        raise ValueError(f"not a canonical nuclide name: {name!r}")
    return Z_OF[m.group(1)] * 1000 + int(m.group(2)), int(m.group(3) or 0)


def mean_beta_energy_eV(rec):
    """Sum of the STYP=1 (beta-minus) spectra's average energies; None when the record has none."""
    total = sum(s["average_energy"] for s in rec["spectra"] if s["styp"] == 1)
    return total if math.isfinite(total) and total > 0.0 else None


# ------------------------------------------------------------------------------ response curves, Eq. 63


def curve_value(curve, energy):
    """Log-log interpolation. A repeated abscissa (absorption edge) returns the last of the equal entries at that
    energy exactly, and leaves from it above (docs: photon response)."""
    es, vs = curve
    if energy < es[0] or energy > es[-1]:
        return None
    upper = bisect.bisect_right(es, energy)
    if upper > 0 and es[upper - 1] == energy:
        return vs[upper - 1]
    if upper == 0 or upper == len(es):
        return None
    x0, x1, y0, y1 = es[upper - 1], es[upper], vs[upper - 1], vs[upper]
    if x1 == x0:
        return y1
    t = math.log(energy / x0) / math.log(x1 / x0)
    return math.exp(math.log(y0) + t * math.log(y1 / y0))


def load_response(path):
    doc = json.loads(Path(path).read_text())
    air = doc["air_mass_energy_absorption"]
    mu = doc.get("air_mass_attenuation")
    return {"path": str(path), "sha256": sha256_file(path),
            "mu_en": (air["energy_eV"], air["values_cm2_g"]),
            "mu": (mu["energy_eV"], mu["values_cm2_g"]) if mu else None}


def dose_terms(groups, reference_power_W_g, mass_g, resp, r, air, rho):
    """Manual Eq. (63) over `groups`, each at its centroid, no build-up:
        D = sum_i mu_en(E_i) exp(-mu(E_i) r) P_i / (4 pi r^2)
    with mu_en [cm2/g -> 0.1 m2/kg], mu = (mu/rho) rho [per cm -> 100 per m], P_i = power_W_g * mass, and a factor
    3600 for Sv/h. Returns the raw sum, whether the dose is reportable, the excluded/sub-threshold power and the
    per-group contributions (for the ratio check). `unavailable` when air transmission lacks its curve."""
    if air and resp["mu"] is None:
        return {"unavailable": True}
    e_lo = resp["mu_en"][0][0]
    if air:
        e_lo = max(e_lo, resp["mu"][0][0])
    dose = subthreshold = excluded = grouped = 0.0
    contributions = []
    for index, g in enumerate(groups):
        grouped += g["power_W_g"]
        power_W = g["power_W_g"] * mass_g
        if power_W <= 0.0:
            continue
        e = g["centroid_eV"]
        if e < e_lo:
            subthreshold += power_W
            continue
        mu_en = curve_value(resp["mu_en"], e)
        if mu_en is None:
            excluded += power_W
            continue
        transmission, mu_per_m = 1.0, 0.0
        if air:
            mu_rho = curve_value(resp["mu"], e)
            if mu_rho is None:
                excluded += power_W
                continue
            mu_per_m = mu_rho * rho * 100.0
            transmission = math.exp(-mu_per_m * r)
        d = mu_en * 0.1 * transmission * power_W / (4.0 * math.pi * r * r) * 3600.0
        dose += d
        contributions.append({"group": index, "centroid_eV": e, "dose": d, "mu_per_m": mu_per_m})
    ungrouped = max(reference_power_W_g - grouped, 0.0)
    if ungrouped > UNGROUPED_TOL * reference_power_W_g:
        excluded += ungrouped * mass_g
    total_W = reference_power_W_g * mass_g
    complete = excluded == 0.0 and subthreshold <= SUBTHRESHOLD_TOL * total_W
    return {"unavailable": False, "dose_raw": dose, "dose": dose if complete else None, "complete": complete,
            "subthreshold_W": subthreshold, "excluded_W": excluded, "contributions": contributions}


def check_point_source_block(accs, tag, block, terms, point, mass_g):
    """The emitted `point_source_dose` block against the recomputed terms."""
    accs.check("point.block_present", tag, block is not None, True)
    if block is None:
        return
    accs.check("point.distance_m", tag, block["distance_m"], point["distance_m"])
    accs.check("point.mass_g", tag, block["mass_g"], mass_g)
    accs.check("point.air_attenuation", tag, block["air_attenuation"], point["air"])
    accs.check("point.air_density_g_cm3", tag, block.get("air_density_g_cm3"),
               point["rho"] if point["air"] else None)
    if terms["unavailable"]:
        accs.check("point.unavailable.dose_null", tag, block["gamma_air_dose_Sv_h"], None)
        accs.check("point.unavailable.per_g_null", tag, block["gamma_air_dose_Sv_h_per_g"], None)
        accs.check("point.unavailable.reason_names_curve", tag,
                   "air_mass_attenuation" in (block.get("unavailable_reason") or ""), True)
        accs.check("point.unavailable.subthreshold", tag, block["subthreshold_power_W"], 0.0)
        accs.check("point.unavailable.excluded", tag, block["excluded_power_W"], 0.0)
        return
    accs.check("point.dose_Sv_h", tag, block["gamma_air_dose_Sv_h"], terms["dose"])
    accs.check("point.dose_Sv_h_per_g", tag, block["gamma_air_dose_Sv_h_per_g"],
               None if terms["dose"] is None else terms["dose"] / mass_g)
    accs.check("point.subthreshold_power_W", tag, block["subthreshold_power_W"], terms["subthreshold_W"])
    accs.check("point.excluded_power_W", tag, block["excluded_power_W"], terms["excluded_W"])
    accs.check("point.reason_present_iff_null", tag, block.get("unavailable_reason") is not None,
               terms["dose"] is None)


def check_point_source_run(accs, label, result, resp, point, mass_g):
    """G3 for one run, every step: the total dose and every nuclide's dose. Returns the per-step terms."""
    steps_out = []
    for i, step in enumerate(result["steps"]):
        ps = step.get("photon_source")
        tag = f"{label}/step{i}"
        accs.check("photon_source_present", tag, ps is not None, True)
        if ps is None:
            steps_out.append(None)
            continue
        accs.check("source_power_W_scales_with_mass", tag, ps["source_power_W"], ps["source_power_W_g"] * mass_g)
        terms = dose_terms(ps["groups"], ps["source_power_W_g"], mass_g, resp, point["distance_m"], point["air"],
                           point["rho"])
        check_point_source_block(accs, tag, ps.get("point_source_dose"), terms, point, mass_g)
        nuclide_terms = {}
        for n in ps["by_nuclide"]:
            nt = dose_terms(n["groups"], n["source_power_W_g"], mass_g, resp, point["distance_m"], point["air"],
                            point["rho"])
            nuclide_terms[n["nuclide"]] = nt
            w = f"{tag}/{n['nuclide']}"
            if nt["unavailable"]:
                accs.check("point.nuclide.unavailable_absent", w, n.get("point_source_gamma_air_dose_Sv_h"), None)
            else:
                accs.check("point.nuclide.dose_Sv_h", w, n.get("point_source_gamma_air_dose_Sv_h"), nt["dose"])
        steps_out.append({"total": terms, "nuclides": nuclide_terms,
                          "emitted": (ps.get("point_source_dose") or {}).get("gamma_air_dose_Sv_h"),
                          "emitted_nuclides": {n["nuclide"]: n.get("point_source_gamma_air_dose_Sv_h")
                                               for n in ps["by_nuclide"]}})
    return steps_out


def check_distance_ratio(accs, label, run1, run3, terms1, terms3, mass_g):
    """1 m over 3 m: per group 9 exp(2 mu) with mu in 1/m, for the total source and for every nuclide; the emitted
    doses' ratio must lie between the smallest and largest group ratio, and a source with one dose-bearing group
    must give that group's ratio exactly."""
    n_groups = n_exact = 0
    worst_group = 0.0
    for i, (s1, s3) in enumerate(zip(run1["steps"], run3["steps"])):
        t1, t3 = terms1[i], terms3[i]
        if t1 is None or t3 is None:
            continue
        tag = f"{label}/step{i}"
        accs.check("ratio.groups_identical_between_distances", tag, s1["photon_source"]["groups"],
                   s3["photon_source"]["groups"])
        sets = [("total", t1["total"], t3["total"], t1["emitted"], t3["emitted"])]
        for name, nt1 in t1["nuclides"].items():
            sets.append((name, nt1, t3["nuclides"][name], t1["emitted_nuclides"].get(name),
                         t3["emitted_nuclides"].get(name)))
        for name, a, b, emitted1, emitted3 in sets:
            if a["unavailable"] or b["unavailable"]:
                continue
            by_group3 = {c["group"]: c for c in b["contributions"]}
            ratios = []
            for c1 in a["contributions"]:
                c3 = by_group3.get(c1["group"])
                if c3 is None or c3["dose"] <= 0.0:
                    continue
                expected = 9.0 * math.exp(2.0 * c1["mu_per_m"])
                got = c1["dose"] / c3["dose"]
                accs.check("ratio.group_1m_over_3m", f"{tag}/{name}/g{c1['group']}", got, expected)
                worst_group = max(worst_group, rel_diff(got, expected))
                ratios.append(expected)
                n_groups += 1
            if emitted1 is not None and emitted3 is not None and emitted3 > 0.0 and ratios:
                got = emitted1 / emitted3
                slack = 1.0 + 1e-9
                accs.check("ratio.emitted_within_group_range", f"{tag}/{name}",
                           min(ratios) / slack <= got <= max(ratios) * slack, True)
                if len(ratios) == 1:
                    accs.check("ratio.emitted_single_group_exact", f"{tag}/{name}", got, ratios[0])
                    n_exact += 1
                # the total dose ratio from the recomputed contributions
                if a["dose_raw"] > 0.0 and b["dose_raw"] > 0.0:
                    accs.check("ratio.emitted_vs_recomputed", f"{tag}/{name}", got, a["dose_raw"] / b["dose_raw"])
    return {"groups_checked": n_groups, "single_group_exact_checks": n_exact, "worst_group_relative": worst_group}


# ------------------------------------------------------------------------------ Steiner 22-group binning


def expected_steiner_boundaries():
    return [int(round(v * 1e6)) for v in STEINER_22_MEV]


def line_group(energy, boundaries):
    """photon.rs line_group: [b_g, b_{g+1}) with the top boundary inclusive in the last group."""
    if energy < boundaries[0] or energy > boundaries[-1]:
        return None
    if energy == boundaries[-1]:
        return len(boundaries) - 2
    return bisect.bisect_right(boundaries, energy) - 1


def steiner_expected_groups(rec, boundaries):
    """Photons per decay and eV per decay in each group from the record's discrete STYP 0/9 lines, scaled so the
    energy equals the record's mean electromagnetic energy. None for a record whose photon spectrum has a
    continuum (this checker does not integrate continua), or without a photon spectrum."""
    ng = len(boundaries) - 1
    count, moment = [0.0] * ng, [0.0] * ng
    raw_energy = 0.0
    any_line = False
    for s in rec["spectra"]:
        if s["styp"] not in (0, 9):
            continue
        if s["has_continuum"]:
            return None
        for energy, intensity in s["discrete"]:
            y = s["fd"] * intensity
            if y <= 0.0 or energy < 0.0:
                continue
            any_line = True
            raw_energy += y * energy
            g = line_group(energy, boundaries)
            if g is not None:
                count[g] += y
                moment[g] += y * energy
    if not any_line or raw_energy <= 0.0:
        return "no_spectrum"
    e_em = rec["energies"][2] if len(rec["energies"]) > 2 else 0.0
    scale = e_em / raw_energy if e_em > 0.0 else 1.0
    return [c * scale for c in count], [m * scale for m in moment]


def check_steiner_run(accs, label, result, records):
    ps0 = result["steps"][0]["photon_source"]
    expected_b = expected_steiner_boundaries()
    accs.check("steiner.group_structure", label, ps0["group_structure"], "steiner-22")
    accs.check("steiner.boundaries_eV", label, [int(round(b)) for b in ps0["boundaries_eV"]], expected_b)
    accs.check("steiner.boundaries_exact_floats", label, ps0["boundaries_eV"], [float(b) for b in expected_b])
    accs.check("steiner.group_count", label, len(ps0["groups"]), 22)
    skipped, checked = {}, 0
    for i, step in enumerate(result["steps"]):
        ps = step["photon_source"]
        tag = f"{label}/step{i}"
        grouped_W_g = sum(g["power_W_g"] for g in ps["groups"])
        accs.check("steiner.power_closure", tag, ps["source_power_W_g"] - ps["ungrouped_power_W_g"], grouped_W_g)
        for g, lo, hi in zip(ps["groups"], expected_b, expected_b[1:]):
            accs.check("steiner.group_edges", tag, [g["low_eV"], g["high_eV"]], [float(lo), float(hi)])
        for n in ps["by_nuclide"]:
            rec = records.get(key_of(n["nuclide"]))
            exp = steiner_expected_groups(rec, expected_b) if rec else "no_record"
            if exp is None or isinstance(exp, str):
                skipped[n["nuclide"]] = exp or "continuum"
                continue
            checked += 1
            w = f"{tag}/{n['nuclide']}"
            for g, ec, em in zip(n["groups"], exp[0], exp[1]):
                accs.check("steiner.nuclide_group_photons_s_g", w, g["photons_s_g"], n["activity_Bq_g"] * ec)
                accs.check("steiner.nuclide_group_power_W_g", w, g["power_W_g"], n["activity_Bq_g"] * em * EV_J)
    return {"nuclide_steps_checked": checked, "nuclides_not_recomputed": dict(sorted(skipped.items()))}


# ------------------------------------------------------------------------------ bremsstrahlung, Eqs. 65-68


def D(x):
    return decimal.Decimal(repr(float(x)))


def brems_photons_per_electron(z, e0_keV, lo_keV, hi_keV):
    """Photons per electron of mean energy E0 in [lo, hi] keV, manual Eq. (66), evaluated in 60-digit decimals so
    the cancellation in E0 ln(top/lo) - (top - lo) cannot cost digits:
        a Z [E0 ln(min(E0, hi)/lo) - (min(E0, hi) - lo)]  for E0 > lo, else 0."""
    if e0_keV <= lo_keV:
        return 0.0
    top = min(D(e0_keV), D(hi_keV))
    lo, e0 = D(lo_keV), D(e0_keV)
    return float(D(BREMS_A_PER_KEV) * D(z) * (e0 * (top / lo).ln() - (top - lo)))


def brems_energy_below_keV(z, e0_keV, hi_keV):
    """Energy (keV per electron) the Eq. (65) spectrum a Z (E0/E - 1) puts below hi: a Z (E0 top - top^2/2)."""
    top = min(D(e0_keV), D(hi_keV))
    return float(D(BREMS_A_PER_KEV) * D(z) * (D(e0_keV) * top - top * top / 2))


def brems_expected(step_act, requested, records, z, boundaries, mass_g):
    """Everything the bremsstrahlung block should hold at one step."""
    ng = len(boundaries) - 1
    by_nuclide, missing = {}, {}
    agg_count, agg_moment = [0.0] * ng, [0.0] * ng
    zero_edge = 0.0
    for name in requested:
        activity = step_act.get(name, 0.0)
        if activity <= 0.0:
            continue
        rec = records.get(key_of(name))
        e0 = mean_beta_energy_eV(rec) if rec else None
        if e0 is None:
            missing[name] = "beta"
            continue
        e0_keV = e0 / 1000.0
        groups = []
        photons = moment_total = 0.0
        for g in range(ng):
            lo, hi = boundaries[g], boundaries[g + 1]
            if lo <= 0.0:
                zero_edge += activity * brems_energy_below_keV(z, e0_keV, hi / 1000.0) * 1000.0 * EV_J
                groups.append((0.0, 0.0))
                continue
            rate = activity * brems_photons_per_electron(z, e0_keV, lo / 1000.0, hi / 1000.0)
            moment = rate * 0.5 * (lo + hi)  # Eq. (67): the bin-midpoint energy
            agg_count[g] += rate
            agg_moment[g] += moment
            photons += rate
            moment_total += moment
            groups.append((rate, moment))
        by_nuclide[name] = {"e0_eV": e0, "activity": activity, "groups": groups, "photons_s_g": photons,
                            "power_W_g": moment_total * EV_J}
    return {"by_nuclide": by_nuclide, "missing": missing, "agg_count": agg_count, "agg_moment": agg_moment,
            "zero_edge_W_g": zero_edge, "photons_s_g": sum(agg_count), "power_W_g": sum(agg_moment) * EV_J,
            "without_activity": [n for n in requested if step_act.get(n, 0.0) <= 0.0]}


def check_group_list(accs, kind, tag, emitted_groups, boundaries, counts, moments, mass_g):
    accs.check(f"{kind}.group_count", tag, len(emitted_groups), len(boundaries) - 1)
    for g, (lo, hi, c, m) in enumerate(zip(boundaries, boundaries[1:], counts, moments)):
        if g >= len(emitted_groups):
            break
        e = emitted_groups[g]
        w = f"{tag}/g{g}"
        accs.check(f"{kind}.group_edges", w, [e["low_eV"], e["high_eV"]], [lo, hi])
        accs.check(f"{kind}.group_photons_s_g", w, e["photons_s_g"], c)
        accs.check(f"{kind}.group_photons_s", w, e["photons_s"], c * mass_g)
        accs.check(f"{kind}.group_power_W_g", w, e["power_W_g"], m * EV_J)
        accs.check(f"{kind}.group_power_W", w, e["power_W"], m * EV_J * mass_g)
        accs.check(f"{kind}.group_centroid_eV", w, e["centroid_eV"], 0.5 * (lo + hi) if c > 0.0 else 0.0)


def check_brems_run(accs, label, result, requested, records, z_expected, mass_g, resp=None, point=None):
    """G4 for one run: recompute every step's bremsstrahlung block. `z_expected` is the atom-fraction mean Z."""
    n_nuclide_steps = 0
    for i, step in enumerate(result["steps"]):
        ps = step["photon_source"]
        tag = f"{label}/step{i}"
        b = ps.get("bremsstrahlung")
        accs.check("brems.block_present", tag, b is not None, True)
        if b is None:
            continue
        boundaries = ps["boundaries_eV"]
        exp = brems_expected(step["activity_Bq_per_g"], requested, records, z_expected, boundaries, mass_g)
        accs.check("brems.model", tag, b["model"], BREMS_MODEL)
        accs.check("brems.z_eff", tag, b["z_eff"], z_expected)
        accs.check("brems.z_eff_available", tag, b["z_eff_unavailable_reason"], None)
        accs.check("brems.requested_without_activity", tag, b["requested_nuclides_without_activity"],
                   exp["without_activity"])
        accs.check("brems.missing_input_nuclides", tag, sorted(m["nuclide"] for m in b["missing_inputs"]),
                   sorted(exp["missing"]))
        for m in b["missing_inputs"]:
            accs.check("brems.missing_input_names_beta_energy", f"{tag}/{m['nuclide']}",
                       "mean beta-minus energy" in m["missing"], True)
        accs.check("brems.inputs_complete", tag, b["inputs_complete"], not exp["missing"])
        accs.check("brems.nuclide_set", tag, sorted(n["nuclide"] for n in b["by_nuclide"]),
                   sorted(exp["by_nuclide"]))
        accs.check("brems.photons_s_g", tag, b["photons_s_g"], exp["photons_s_g"])
        accs.check("brems.photons_s", tag, b["photons_s"], exp["photons_s_g"] * mass_g)
        accs.check("brems.power_W_g", tag, b["power_W_g"], exp["power_W_g"])
        accs.check("brems.power_W", tag, b["power_W"], exp["power_W_g"] * mass_g)
        accs.check("brems.zero_lower_edge_group_power_W_g", tag, b["zero_lower_edge_group_power_W_g"],
                   exp["zero_edge_W_g"])
        if boundaries[0] <= 0.0:
            accs.check("brems.zero_edge_group_empty", tag,
                       [b["groups"][0]["photons_s_g"], b["groups"][0]["power_W_g"]], [0.0, 0.0])
        check_group_list(accs, "brems.total", tag, b["groups"], boundaries, exp["agg_count"], exp["agg_moment"],
                         mass_g)
        for n in b["by_nuclide"]:
            e = exp["by_nuclide"].get(n["nuclide"])
            if e is None:
                continue
            n_nuclide_steps += 1
            w = f"{tag}/{n['nuclide']}"
            accs.check("brems.nuclide.activity_Bq_g", w, n["activity_Bq_g"], e["activity"])
            accs.check("brems.nuclide.mean_beta_energy_eV", w, n["mean_beta_energy_eV"], e["e0_eV"])
            accs.check("brems.nuclide.photons_s_g", w, n["photons_s_g"], e["photons_s_g"])
            accs.check("brems.nuclide.power_W_g", w, n["power_W_g"], e["power_W_g"])
            check_group_list(accs, "brems.nuclide", w, n["groups"], boundaries, [c for c, _ in e["groups"]],
                             [m for _, m in e["groups"]], mass_g)
        if resp is not None and point is not None:
            terms = dose_terms(b["groups"], b["power_W_g"], mass_g, resp, point["distance_m"], point["air"],
                               point["rho"])
            if terms["unavailable"]:
                accs.check("brems.point_source.unavailable_absent", tag, b.get("point_source_gamma_air_dose_Sv_h"),
                           None)
            else:
                accs.check("brems.point_source.dose_Sv_h", tag, b.get("point_source_gamma_air_dose_Sv_h"),
                           terms["dose"])
                for n in b["by_nuclide"]:
                    nt = dose_terms(n["groups"], n["power_W_g"], mass_g, resp, point["distance_m"], point["air"],
                                    point["rho"])
                    accs.check("brems.point_source.nuclide_dose_Sv_h", f"{tag}/{n['nuclide']}",
                               n.get("point_source_gamma_air_dose_Sv_h"), nt["dose"])
    return {"nuclide_steps_checked": n_nuclide_steps}


def strip_brems(result):
    """The result without the bremsstrahlung component and without the spec-derived ledger/certificate/timing."""
    out = copy.deepcopy(result)
    for key in ("ms", "ledger", "certificate", "spec_title"):
        out.pop(key, None)
    for step in out["steps"]:
        (step.get("photon_source") or {}).pop("bremsstrahlung", None)
    return out


# ------------------------------------------------------------------------------ A2 index


def a2_expected(activity, coefficients, kind):
    """sum of the kind's contribution over the nuclides with a coefficient, in sorted-name order, plus the
    covered/missing bookkeeping. waste_index/clearance_index: 1000 act / limit; a2: act / A2."""
    factor = 1.0 if kind == "a2" else 1000.0
    terms, covered, missing_act, missing = [], 0.0, 0.0, []
    total = math.fsum(activity.values())
    for name in sorted(activity):
        act = activity[name]
        if act <= 0.0:
            continue
        limit = coefficients.get(name)
        if limit is None:
            missing_act += act
            missing.append(name)
        else:
            terms.append(factor * act / limit)
            covered += act
    return {"value": math.fsum(terms), "covered": covered, "missing_activity": missing_act, "missing": missing,
            "contributing": len(terms), "coverage": covered / total if total > 0.0 else 1.0}


def check_a2_run(accs, label, result, table_doc, response_ids):
    """Evaluate the emitted radiological block of every step against the table."""
    by_id = {r["id"]: r for r in table_doc["responses"]}
    unit_of = {"a2": "A2_per_g_material", "waste_index": "dimensionless", "clearance_index": "dimensionless"}
    listed = {}
    for i, step in enumerate(result["steps"]):
        block = step.get("radiological")
        tag = f"{label}/step{i}"
        accs.check("a2.block_present", tag, block is not None, True)
        if block is None:
            continue
        accs.check("a2.response_ids", tag, [r["id"] for r in block["responses"]], list(response_ids))
        for emitted in block["responses"]:
            resp = by_id.get(emitted["id"])
            if resp is None:
                continue
            kind = resp["kind"]
            exp = a2_expected(step["activity_Bq_per_g"], resp["coefficients"], kind)
            w = f"{tag}/{emitted['id']}"
            accs.check("a2.value", w, emitted["value"], exp["value"])
            accs.check("a2.unit", w, emitted["unit"], unit_of.get(kind, emitted["unit"]))
            accs.check("a2.covered_activity_Bq_per_g", w, emitted["covered_activity_Bq_per_g"], exp["covered"])
            accs.check("a2.missing_activity_Bq_per_g", w, emitted["missing_activity_Bq_per_g"],
                       exp["missing_activity"])
            accs.check("a2.activity_coverage_fraction", w, emitted["activity_coverage_fraction"], exp["coverage"])
            accs.check("a2.contributing_nuclide_count", w, emitted["contributing_nuclide_count"],
                       exp["contributing"])
            accs.check("a2.missing_active_nuclides", w, emitted["missing_active_nuclides"], exp["missing"])
            for name in exp["missing"]:
                entry = listed.setdefault(emitted["id"], {})
                entry[name] = max(entry.get(name, 0.0), step["activity_Bq_per_g"][name])
    cert = (result.get("certificate") or {}).get("radiological")
    if cert is not None:
        for entry in cert.get("responses", []):
            resp = by_id.get(entry.get("id"))
            if resp is None:
                continue
            accs.check("a2.certificate_kind", entry["id"], entry.get("kind"), resp["kind"])
            accs.check("a2.certificate_coefficient_count", entry["id"], entry.get("coefficient_count"),
                       len(resp["coefficients"]))
            if resp["kind"] == "a2":
                accs.check("a2.certificate_formula", entry["id"], entry.get("formula"),
                           "sum(activity_Bq_per_g / A2_Bq)")
    return {rid: dict(sorted(v.items(), key=lambda kv: -kv[1])) for rid, v in listed.items()}


# ------------------------------------------------------------------------------ running binaries and scripts


class Runner:
    """Serial subprocess calls, no shell, each under a timeout; every run lands in `record["runs"]`."""

    def __init__(self, workdir, timeout, reuse, record):
        self.workdir = workdir
        self.timeout = timeout
        self.reuse = reuse
        self.record = record

    def write_spec(self, label, spec):
        path = self.workdir / f"{label}.spec.json"
        path.write_text(json.dumps(spec, indent=1) + "\n")
        return path

    def run_spec(self, label, binary, spec_path, cwd):
        out_file = self.workdir / f"{label}.json"
        log_file = self.workdir / f"{label}.log"
        info = {"binary": str(binary), "spec": record_file(spec_path), "cwd": str(cwd)}
        self.record["runs"][label] = info
        if not (self.reuse and out_file.exists()):
            started = time.monotonic()
            try:
                with open(log_file, "wb") as log:
                    proc = subprocess.run([str(binary), "run", str(spec_path), str(out_file)], cwd=str(cwd),
                                          stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                          timeout=self.timeout)
            except subprocess.TimeoutExpired:
                info["error"] = f"timed out after {self.timeout} s"
                return None
            except OSError as error:
                info["error"] = f"{type(error).__name__}: {error}"
                return None
            info["seconds"] = round(time.monotonic() - started, 3)
            info["exit_code"] = proc.returncode
            if proc.returncode != 0:
                tail = log_file.read_bytes()[-400:].decode("utf-8", errors="replace")
                info["error"] = f"exit code {proc.returncode}; see {log_file}: ...{tail.strip()}"
                return None
        info["output"] = record_file(out_file)
        try:
            return json.loads(out_file.read_text())
        except ValueError as error:
            info["error"] = f"output is not JSON: {error}"
            return None

    def run_script(self, label, args, cwd, timeout=None):
        """Python helper script under `python3 -I`. Returns (exit code, stdout, stderr); None on timeout."""
        info = {"command": [sys.executable, "-I", *map(str, args)], "cwd": str(cwd)}
        self.record["runs"][label] = info
        started = time.monotonic()
        try:
            proc = subprocess.run(info["command"], cwd=str(cwd), capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=timeout or self.timeout)
        except subprocess.TimeoutExpired:
            info["error"] = "timed out"
            return None
        except OSError as error:
            info["error"] = f"{type(error).__name__}: {error}"
            return None
        info["seconds"] = round(time.monotonic() - started, 3)
        info["exit_code"] = proc.returncode
        info["stdout_tail"] = proc.stdout[-1500:]
        if proc.returncode != 0:
            info["error"] = proc.stderr[-1500:]
        return proc.returncode, proc.stdout, proc.stderr


def absolutize_paths(node):
    """Resolve relative data paths against the repo root, in place (as controls/check_p122.py)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("path", "primary", "fallback", "overrides", "file") and isinstance(v, str) and v \
                    and not v.startswith(("catalog:", "bundled:")) and not Path(v).is_absolute() \
                    and (ROOT / v).exists():
                node[k] = str((ROOT / v).resolve())
            else:
                absolutize_paths(v)
    elif isinstance(node, list):
        for v in node:
            absolutize_paths(v)


def fns_variant(base_spec, suffix, mass_g=None, photon=None, radiological=None, composition=None):
    spec = copy.deepcopy(base_spec)
    absolutize_paths(spec)
    if mass_g is not None:
        spec["material"]["mass_g"] = mass_g
    if composition is not None:
        spec["material"]["composition"] = composition
    if photon is not None:
        spec["photon"] = photon
    if radiological is not None:
        spec["radiological"] = radiological
    spec["title"] = f"{spec.get('title', 'FNS Fe')} [P123 {suffix}]"
    return spec


def hashed(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def masked_text(path):
    """Result text with every `"ms": <number>` field masked, and how many were masked."""
    text = Path(path).read_text()
    return re.subn(r'"ms"\s*:\s*[-+0-9.eE]+', '"ms":0', text)


def compare_results(ref_path, cand_path):
    ref_text, n_ref = masked_text(ref_path)
    cand_text, n_cand = masked_text(cand_path)
    ref, cand = json.loads(Path(ref_path).read_text()), json.loads(Path(cand_path).read_text())
    for r in (ref, cand):
        r.pop("ms", None)
    out = {"bytes_equal_after_masking_ms": ref_text == cand_text, "ms_fields_masked": [n_ref, n_cand],
           "canonical_equal_without_ms": canonical(ref) == canonical(cand), "steps": len(ref.get("steps", []))}
    if not out["canonical_equal_without_ms"]:
        out["first_differences"] = first_differences(ref, cand)
    out["identical"] = bool(out["bytes_equal_after_masking_ms"] and out["canonical_equal_without_ms"])
    return out


# ------------------------------------------------------------------------------ G2


def discover_example_specs():
    """Example specs that set a `photon` or `radiological` section (top level), or None-found."""
    found, listed = [], []
    for path in sorted((ROOT / "examples").rglob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("spec") == "actinv-spec-1":
            listed.append(str(path.relative_to(ROOT)))
            if doc.get("photon") is not None or doc.get("radiological") is not None:
                found.append(path)
    return found, listed


def legacy_radiological_table(path):
    """A small table of the pre-P123 kinds, hash-pinned by the spec that uses it."""
    doc = {"format": "actinv-radiological-table-1", "title": "P123 checker legacy-kind fixture",
           "source": {"citation": "synthetic fixture", "edition": "1", "url": "https://example.invalid/p123",
                      "jurisdiction": "none"},
           "responses": [
               {"id": "clear", "kind": "clearance_index", "basis": "fixture limits, Bq/kg",
                "coefficients": {"Mn56": 1.0e6, "Mn54": 1.0e3, "Co60": 1.0e2, "Fe55": 1.0e6}},
               {"id": "ingest", "kind": "ingestion_dose", "basis": "fixture Sv/Bq",
                "coefficients": {"Mn56": 2.4e-10, "Mn54": 7.1e-10, "Fe55": 3.3e-10}}]}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return path


def nist_air_rows(path):
    """(energy eV, mu/rho, mu_en/rho) from the NIST dry-air page, read here independently of the builder."""
    text = Path(path).read_text(encoding="latin-1")
    pre = re.search(r"<PRE>(.*?)</PRE>", text, re.S | re.I).group(1)
    rows = []
    for line in pre.splitlines():
        m = re.match(r"^\s*(?:\d+\s+[A-Z]\s+)?(\d\.\d+E[-+]\d+)\s+(\d\.\d+E[-+]\d+)\s+(\d\.\d+E[-+]\d+)\s*$", line)
        if m:
            rows.append((float(m.group(1)) * 1e6, float(m.group(2)), float(m.group(3))))
    return rows


def g2_builder(runner, workdir, nist_cache, installed_fe, installed_all):
    out = {"nist_cache": str(nist_cache)}
    if not (nist_cache.is_dir() and (nist_cache / "air.html").exists() and (nist_cache / "z26.html").exists()):
        out.update(status="not_run", reason="the NIST page cache (air.html, z26.html) is not available locally; "
                                            "the builder needs network access without it")
        return out
    ref_script = workdir / "ref_build_photon_response.py"
    shown = subprocess.run(["git", "show", f"{BASE_COMMIT}:scripts/build_photon_response.py"], cwd=str(ROOT),
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    if shown.returncode != 0:
        out.update(status="not_run", reason=f"git show {BASE_COMMIT}:scripts/build_photon_response.py failed")
        return out
    ref_script.write_bytes(shown.stdout)
    out["reference_script_sha256"] = sha256_file(ref_script)
    out["candidate_script_sha256"] = sha256_file(BUILDER)
    cache_args = ["--cache-dir", str(nist_cache), "--offline"]
    outputs = {}
    for label, script, extra in (("ref_default", ref_script, []), ("cand_default", BUILDER, []),
                                 ("ref_all", ref_script, ["--elements", "all"]),
                                 ("cand_all", BUILDER, ["--elements", "all"]),
                                 ("cand_air", BUILDER, ["--air-attenuation"])):
        path = workdir / f"builder_{label}.json"
        result = runner.run_script(f"builder_{label}", [script, path, *extra, *cache_args], workdir, timeout=600)
        if result is None or result[0] != 0:
            out.update(status="fail", error=f"builder run {label} failed; see runs")
            return out
        outputs[label] = path
    same = lambda a, b: outputs[a].read_bytes() == outputs[b].read_bytes()
    out["default_output_byte_identical_reference_vs_candidate_script"] = same("ref_default", "cand_default")
    out["all_elements_output_byte_identical_reference_vs_candidate_script"] = same("ref_all", "cand_all")
    out["default_output_sha256"] = sha256_file(outputs["cand_default"])
    # the candidate's all-element build against the installed all-element response
    if installed_all is not None and installed_all.exists():
        out["all_elements_vs_installed"] = {
            "installed": str(installed_all), "installed_sha256": sha256_file(installed_all),
            "byte_identical": outputs["cand_all"].read_bytes() == installed_all.read_bytes()}
    # the default Fe build against the installed Fe response named by the specs
    if installed_fe is not None and installed_fe.exists():
        built, installed = (json.loads(p.read_text()) for p in (outputs["cand_default"], installed_fe))
        same_curves = all(built[k] == installed[k] for k in ("schema", "air_mass_energy_absorption",
                                                              "element_mass_attenuation"))
        differing = sorted(k for k in set(built["provenance"]) | set(installed["provenance"])
                           if built["provenance"].get(k) != installed["provenance"].get(k))
        out["default_vs_installed_fe"] = {
            "installed": str(installed_fe), "installed_sha256": sha256_file(installed_fe),
            "byte_identical": outputs["cand_default"].read_bytes() == installed_fe.read_bytes(),
            "schema_and_all_curves_identical": same_curves, "differing_provenance_keys": differing,
            "note": "the installed file records the hash of a NIST page fetched earlier; the cached page differs "
                    "in bytes, the tabulated numbers do not"}
    # the --air-attenuation variant against the default one and the NIST page read independently
    default, air = (json.loads(outputs[k].read_text()) for k in ("cand_default", "cand_air"))
    extra_keys = sorted(set(air) - set(default))
    rest_equal = all(air[k] == default[k] for k in default)
    rows = nist_air_rows(nist_cache / "air.html")
    curve = air.get("air_mass_attenuation", {"energy_eV": [], "values_cm2_g": []})
    mu_en = air["air_mass_energy_absorption"]
    page_ok = (len(rows) == len(curve["energy_eV"]) == len(mu_en["energy_eV"]) and all(
        rel_diff(e, ce) <= 1e-12 and rel_diff(mu, cm) <= 1e-12 and rel_diff(me, cme) <= 1e-12
        for (e, mu, me), ce, cm, cme in zip(rows, curve["energy_eV"], curve["values_cm2_g"],
                                            mu_en["values_cm2_g"])))
    out["air_attenuation_variant"] = {"extra_top_level_keys": extra_keys, "all_other_content_identical": rest_equal,
                                      "rows_in_independent_page_parse": len(rows),
                                      "curves_match_independent_page_parse": page_ok,
                                      "sha256": sha256_file(outputs["cand_air"])}
    ok = (out["default_output_byte_identical_reference_vs_candidate_script"]
          and out["all_elements_output_byte_identical_reference_vs_candidate_script"]
          and out.get("all_elements_vs_installed", {"byte_identical": True})["byte_identical"]
          and out.get("default_vs_installed_fe", {"schema_and_all_curves_identical": True})[
              "schema_and_all_curves_identical"]
          and extra_keys == ["air_mass_attenuation"] and rest_equal and page_ok)
    out["status"] = "pass" if ok else "fail"
    out["air_response"] = str(outputs["cand_air"])
    return out


def gate_g2(runner, ref_bin, cand_bin, base_spec, workdir, installed_fe, nist_cache, installed_all):
    out = {"specs": [], "reference_commit": BASE_COMMIT}
    found, listed = discover_example_specs()
    out["example_specs_scanned"] = listed
    out["example_specs_with_photon_or_radiological"] = [str(p.relative_to(ROOT)) for p in found]
    cases = [("fns_fe_5min", GATE_SPEC, ROOT, True)]
    if installed_fe is not None and installed_fe.exists():
        spec = fns_variant(base_spec, "G2 installed photon response",
                           photon={"response": hashed(installed_fe)})
        cases.append(("fns_photon_installed", runner.write_spec("g2_fns_photon_installed", spec), workdir, True))
    else:
        out["fns_photon_installed"] = {"status": "not_run", "reason": f"{installed_fe} not found"}
    table = legacy_radiological_table(workdir / "g2_legacy_radiological_table.json")
    spec = fns_variant(base_spec, "G2 legacy radiological table",
                       radiological={"table": hashed(table), "responses": [], "require_complete": False})
    cases.append(("fns_radiological_legacy", runner.write_spec("g2_fns_radiological_legacy", spec), workdir, True))
    for path in found:
        cases.append((f"example_{path.stem}", path, ROOT, False))
    for name, spec_path, cwd, required in cases:
        ref = runner.run_spec(f"g2_{name}_reference", ref_bin, spec_path, cwd)
        cand = runner.run_spec(f"g2_{name}_candidate", cand_bin, spec_path, cwd)
        entry = {"name": name, "spec": str(spec_path), "required": required}
        if ref is None or cand is None:
            entry["accepted_by_both"] = False
            entry["identical"] = False
            entry["note"] = "a run failed; see runs" + ("" if required else " (an example one binary does not accept)")
        else:
            entry["accepted_by_both"] = True
            entry.update(compare_results(workdir / f"g2_{name}_reference.json", workdir / f"g2_{name}_candidate.json"))
        out["specs"].append(entry)
    out["builder"] = g2_builder(runner, workdir, nist_cache, installed_fe, installed_all)
    specs_ok = all(e["identical"] for e in out["specs"] if e["required"] or e["accepted_by_both"])
    out["not_run"] = [k for k, v in (("builder", out["builder"]), ("fns_photon_installed",
                                                                  out.get("fns_photon_installed", {})))
                      if v.get("status") == "not_run"]
    out["pass"] = bool(specs_ok and out["builder"]["status"] in ("pass", "not_run"))
    return out


# ------------------------------------------------------------------------------ G3, G4


def point_cfg(distance_m, air, rho=AIR_DENSITY_DEFAULT):
    return {"distance_m": distance_m, "air": air, "rho": rho}


def collect_active(results):
    """nuclide -> largest activity (Bq/g) over every step of the given results."""
    best = {}
    for res in results:
        if res is None:
            continue
        for step in res["steps"]:
            for name, act in step["activity_Bq_per_g"].items():
                best[name] = max(best.get(name, 0.0), act)
    return best


def decay_paths(spec):
    decay = spec.get("decay", {})
    return decay.get("primary"), decay.get("fallback") or None


def gate_g3(runner, cand_bin, base_spec, workdir, installed_fe, built_air):
    out = {"mass_g_with_air": POINT_MASS_G, "subchecks": {}}
    runs = {}
    spec_primary, spec_fallback = decay_paths(fns_variant(base_spec, "paths"))
    installed_resp = load_response(installed_fe) if installed_fe is not None and installed_fe.exists() else None
    built_resp = load_response(built_air) if built_air is not None and Path(built_air).exists() else None
    out["responses"] = {"installed": {k: installed_resp[k] for k in ("path", "sha256")} if installed_resp else None,
                        "installed_has_air_mass_attenuation": bool(installed_resp and installed_resp["mu"]),
                        "built_with_air_attenuation": ({k: built_resp[k] for k in ("path", "sha256")}
                                                       if built_resp else None)}

    def run(name, resp, point, mass, structure=None):
        photon = {"response": hashed(resp["path"]),
                  "point_source": {"distance_m": point["distance_m"], "air_attenuation": point["air"]}}
        if point["rho"] != AIR_DENSITY_DEFAULT:
            photon["point_source"]["air_density_g_cm3"] = point["rho"]
        if structure:
            photon["group_structure"] = structure
        spec = fns_variant(base_spec, name, mass_g=mass, photon=photon)
        path = runner.write_spec(name, spec)
        runs[name] = runner.run_spec(name, cand_bin, path, workdir)
        return runs[name]

    plan = []
    if installed_resp:
        plan.append(("g3_installed_air", installed_resp, point_cfg(1.0, True), 1.0, None, "installed_air_unavailable"))
        plan.append(("g3_installed_noair", installed_resp, point_cfg(1.0, False), 1.0, None, "installed_noair"))
    if built_resp:
        plan.append(("g3_built_air_1m", built_resp, point_cfg(1.0, True), POINT_MASS_G, None, "air_1m"))
        plan.append(("g3_built_air_3m", built_resp, point_cfg(3.0, True), POINT_MASS_G, None, "air_3m"))
        plan.append(("g3_steiner_air", built_resp, point_cfg(1.0, True, 1.1e-3), 1.0, "steiner-22", "steiner"))
    not_run = []
    if not installed_resp:
        not_run += ["installed_air_unavailable", "installed_noair"]
    if not built_resp:
        not_run += ["air_1m", "air_3m", "ratio_1m_3m", "steiner"]
    terms = {}
    for name, resp, point, mass, structure, sub in plan:
        res = run(name, resp, point, mass, structure)
        accs = AccSet()
        if res is None:
            out["subchecks"][sub] = {"pass": False, "error": "run failed; see runs"}
            continue
        terms[name] = check_point_source_run(accs, name, res, resp, point, mass)
        entry = accs.summary()
        entry["pass"] = accs.n_bad() == 0 and accs.n() > 0
        entry["steps"] = len(res["steps"])
        steps_null = sum(1 for t in terms[name] if t and not t["total"]["unavailable"] and t["total"]["dose"] is None)
        steps_ok = sum(1 for t in terms[name] if t and not t["total"]["unavailable"] and t["total"]["dose"] is not None)
        entry["steps_with_dose"], entry["steps_with_null_dose"] = steps_ok, steps_null
        if sub == "installed_air_unavailable":
            entry["all_steps_unavailable"] = all(t and t["total"]["unavailable"] for t in terms[name])
            entry["pass"] = entry["pass"] and entry["all_steps_unavailable"]
        else:
            entry["pass"] = entry["pass"] and steps_ok > 0
        if sub == "steiner":
            sa = AccSet()
            needed = {key_of(n["nuclide"]) for s in res["steps"] for n in s["photon_source"]["by_nuclide"]}
            try:
                records = load_decay(spec_primary, spec_fallback, needed)
                entry["grouping"] = check_steiner_run(sa, name, res, records)
                entry["grouping"].update(sa.summary())
                entry["pass"] = entry["pass"] and sa.n_bad() == 0 and sa.n() > 0
            except (OSError, ValueError, KeyError, IndexError) as error:
                entry["grouping"] = {"error": f"{type(error).__name__}: {error}"}
                entry["pass"] = False
        out["subchecks"][sub] = entry
    if "g3_built_air_1m" in terms and "g3_built_air_3m" in terms and runs.get("g3_built_air_1m") \
            and runs.get("g3_built_air_3m"):
        ra = AccSet()
        r1, r3 = runs["g3_built_air_1m"], runs["g3_built_air_3m"]
        ra.check("ratio.step_count", "1m/3m", len(r1["steps"]), len(r3["steps"]))
        ra.check("ratio.step_times", "1m/3m", [s["t_s"] for s in r1["steps"]], [s["t_s"] for s in r3["steps"]])
        info = check_distance_ratio(ra, "1m/3m", r1, r3, terms["g3_built_air_1m"], terms["g3_built_air_3m"],
                                    POINT_MASS_G)
        entry = ra.summary()
        entry.update(info)
        entry["pass"] = ra.n_bad() == 0 and info["groups_checked"] > 0
        out["subchecks"]["ratio_1m_3m"] = entry
    elif "ratio_1m_3m" not in not_run:
        out["subchecks"]["ratio_1m_3m"] = {"pass": False, "error": "a distance run failed"}
    out["not_run"] = not_run
    verdicts = [v["pass"] for v in out["subchecks"].values()]
    out["pass"] = bool(verdicts) and all(verdicts) and not not_run
    if not_run and all(verdicts):
        out["pass"] = None
        out["status"] = "incomplete: sub-checks not run: " + ", ".join(not_run)
    out["_runs"] = runs
    return out


def mixed_z_eff(composition):
    """Atom-fraction mean Z of a wt% composition, from standard atomic weights; also the mass-fraction mean."""
    moles = {el: wt / ATOMIC_WEIGHT[el] for el, wt in composition.items()}
    total_moles = sum(moles.values())
    total_wt = sum(composition.values())
    atom = sum(Z_OF[el] * m for el, m in moles.items()) / total_moles
    mass = sum(Z_OF[el] * wt for el, wt in composition.items()) / total_wt
    return atom, mass


def gate_g4(runner, cand_bin, base_spec, workdir, built_air, g3_runs):
    out = {"subchecks": {}}
    primary, fallback = decay_paths(fns_variant(base_spec, "paths"))
    baseline = g3_runs.get("g3_built_air_1m")
    resp = load_response(built_air) if built_air is not None and Path(built_air).exists() else None
    with_point = baseline is not None and resp is not None
    if not with_point:
        # without the rebuilt air-attenuation response the point-source part of the component is not exercised
        out["point_source_part"] = "not_run: needs the rebuilt air-attenuation response and the G3 1 m run"
        spec = fns_variant(base_spec, "G4 baseline", mass_g=POINT_MASS_G)
        baseline = runner.run_spec("g4_baseline", cand_bin, runner.write_spec("g4_baseline", spec), workdir)
        if baseline is None:
            out["pass"] = False
            out["error"] = "baseline run failed; see runs"
            return out
    fixed = ["Mn56", "Mn57", "Cr55", "Mn58"]
    active = collect_active([baseline])
    # candidate nuclides: the named beta-minus emitters, plus every beta-minus emitter (STYP=1) with real activity,
    # one active nuclide without a beta spectrum (it must be listed as a missing input), one inactive request
    names = set(active) | set(fixed) | {"Cl36", "Fe53"}
    records = load_decay(primary, fallback, {key_of(n) for n in names})
    beta = sorted(n for n in names if records.get(key_of(n)) and mean_beta_energy_eV(records[key_of(n)])
                  and (n in fixed or active.get(n, 0.0) >= MIN_BREMS_ACTIVITY_BQ_G))
    no_beta_active = sorted((n for n in active if records.get(key_of(n)) and not mean_beta_energy_eV(records[key_of(n)])
                             and active[n] >= MIN_BREMS_ACTIVITY_BQ_G), key=lambda n: -active[n])
    inactive = [n for n in ("Cl36", "K42", "Ar42") if active.get(n, 0.0) <= 0.0][:1]
    requested = beta + no_beta_active[:1] + inactive
    out["requested_nuclides"] = requested
    out["named_nuclides_with_styp1_spectrum"] = {n: bool(records.get(key_of(n))
                                                         and mean_beta_energy_eV(records[key_of(n)]))
                                                 for n in fixed}
    out["named_nuclides_peak_activity_Bq_g"] = {n: active.get(n, 0.0) for n in fixed}
    out["expected_missing_input_candidate"] = no_beta_active[:1]
    out["expected_no_activity_candidate"] = inactive
    point = point_cfg(1.0, True)
    photon = {"bremsstrahlung": {"nuclides": requested}}
    if with_point:
        photon["response"] = hashed(resp["path"])
        photon["point_source"] = {"distance_m": point["distance_m"], "air_attenuation": True}
    spec = fns_variant(base_spec, "G4 bremsstrahlung", mass_g=POINT_MASS_G, photon=photon)
    res = runner.run_spec("g4_brems", cand_bin, runner.write_spec("g4_brems", spec), workdir)
    if res is None:
        out["pass"] = False
        out["error"] = "bremsstrahlung run failed; see runs"
        return out
    accs = AccSet()
    info = check_brems_run(accs, "g4_brems", res, requested, records, 26.0, POINT_MASS_G,
                           resp if with_point else None, point if with_point else None)
    entry = accs.summary()
    entry.update(info)
    entry["pass"] = accs.n_bad() == 0 and info["nuclide_steps_checked"] > 0
    out["subchecks"]["fns_fe"] = entry
    # decay-photon fields identical with and without the option
    plain, with_option = strip_brems(baseline), strip_brems(res)
    same = canonical(plain) == canonical(with_option)
    out["subchecks"]["decay_photon_fields_identical"] = {
        "compared": "every result key except ms, ledger, certificate, spec_title, and steps[].photon_source."
                    "bremsstrahlung (same spec and mass, without the option)",
        "pass": same, "first_differences": [] if same else first_differences(plain, with_option),
        "steps_with_bremsstrahlung": sum(1 for s in res["steps"] if s["photon_source"].get("bremsstrahlung"))}
    # a two-element material: Z_eff must be an atom-fraction mean, and the groups follow the reported Z_eff
    composition = {"FE": 90.0, "CR": 10.0}
    z_atom, z_mass = mixed_z_eff({"Fe": 90.0, "Cr": 10.0})
    spec = fns_variant(base_spec, "G4 mixed material", mass_g=1.0, composition=composition,
                       photon={"bremsstrahlung": {"nuclides": requested}})
    res2 = runner.run_spec("g4_brems_fe_cr", cand_bin, runner.write_spec("g4_brems_fe_cr", spec), workdir)
    if res2 is None:
        out["subchecks"]["mixed_material"] = {"pass": False, "error": "run failed; see runs"}
    else:
        reported = res2["steps"][0]["photon_source"]["bremsstrahlung"]["z_eff"]
        accs2 = AccSet()
        info2 = check_brems_run(accs2, "g4_brems_fe_cr", res2, requested, records, reported, 1.0)
        z_tol = 1e-5
        entry2 = accs2.summary()
        entry2.update(info2)
        entry2.update(
            z_eff_reported=reported, z_eff_atom_fraction_from_standard_weights=z_atom,
            z_eff_mass_fraction_mean=z_mass, z_tolerance_relative=z_tol,
            z_note="the engine uses the isotope-resolved natural composition; standard atomic weights agree to the "
                   "tolerance, and the mass-fraction mean is excluded by a wider margin")
        z_ok = rel_diff(reported, z_atom) <= z_tol and rel_diff(reported, z_mass) > 10 * z_tol
        entry2["z_eff_is_atom_fraction_mean"] = z_ok
        entry2["pass"] = accs2.n_bad() == 0 and info2["nuclide_steps_checked"] > 0 and z_ok
        out["subchecks"]["mixed_material"] = entry2
    out["pass"] = all(v["pass"] for v in out["subchecks"].values())
    if out["pass"] and not with_point:
        out["pass"] = None
        out["status"] = "incomplete: the point-source dose of the component was not exercised (no air response)"
    return out


# ------------------------------------------------------------------------------ G6


def a2_fixture_table(path, activity):
    """A table of kind `a2` (and the same limits as `waste_index`) over four fifths of the nuclides seen, so that
    some active nuclides lack a limit."""
    names = sorted(activity)
    coefficients = {}
    for index, name in enumerate(names):
        if index % 5 == 4:
            continue
        coefficients[name] = 1.0e9 * (1 + sum(ord(c) for c in name) % 9)
    doc = {"format": "actinv-radiological-table-1", "title": "P123 checker A2 fixture",
           "source": {"citation": "synthetic fixture", "edition": "1", "url": "https://example.invalid/p123",
                      "jurisdiction": "none"},
           "responses": [{"id": "a2-fixture", "kind": "a2", "basis": "fixture A2 in Bq", "coefficients": coefficients},
                         {"id": "limit-fixture", "kind": "waste_index", "basis": "same limits as a per-kg index",
                          "coefficients": coefficients}]}
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def gate_g6(runner, cand_bin, base_spec, workdir, activity, merged, data_dir):
    out = {"subchecks": {}}
    # --- a table the checker writes: the `a2` kind itself
    if not activity:
        out["subchecks"]["user_table"] = {"pass": False, "error": "no activity map from an earlier run"}
    else:
        table_path = workdir / "g6_a2_fixture_table.json"
        doc = a2_fixture_table(table_path, activity)
        spec = fns_variant(base_spec, "G6 a2 fixture",
                           radiological={"table": hashed(table_path), "responses": ["a2-fixture", "limit-fixture"],
                                         "require_complete": False})
        res = runner.run_spec("g6_user_table", cand_bin, runner.write_spec("g6_user_table", spec), workdir)
        if res is None:
            out["subchecks"]["user_table"] = {"pass": False, "error": "run failed; see runs"}
        else:
            accs = AccSet(A2_REL_TOL)
            listed = check_a2_run(accs, "g6_user_table", res, doc, ["a2-fixture", "limit-fixture"])
            entry = accs.summary()
            entry["tolerance_relative"] = A2_REL_TOL
            entry["nuclides_without_a_limit"] = listed
            entry["pass"] = accs.n_bad() == 0 and accs.n() > 0
            out["subchecks"]["user_table"] = entry
    # --- the bundled table
    if not merged:
        out["subchecks"]["bundled_table"] = {"pass": None, "status": "not_merged",
                                             "reason": "crates/actinv-core/data/regulatory is absent from this tree"}
    else:
        table_path = data_dir / "us-49cfr173-a2.json"
        doc = json.loads(table_path.read_text())
        ids = [r["id"] for r in doc["responses"]]
        entry = {"table": record_file(table_path), "response_ids": ids,
                 "kinds": sorted({r["kind"] for r in doc["responses"]}), "variants": {}}
        for variant, with_sha in (("explicit_sha256", True), ("sha256_filled_by_actinv", False)):
            table_ref = {"path": "bundled:us-49cfr173-a2"}
            if with_sha:
                table_ref["sha256"] = sha256_file(table_path)
            spec = fns_variant(base_spec, f"G6 bundled A2 {variant}",
                               radiological={"table": table_ref, "responses": ids, "require_complete": False})
            res = runner.run_spec(f"g6_bundled_{variant}", cand_bin, runner.write_spec(f"g6_bundled_{variant}", spec),
                                  workdir)
            if res is None:
                entry["variants"][variant] = {"pass": False, "error": "run failed; see runs"}
                continue
            accs = AccSet(A2_REL_TOL)
            listed = check_a2_run(accs, f"g6_bundled_{variant}", res, doc, ids)
            v = accs.summary()
            v["nuclides_without_a_limit"] = listed
            v["pass"] = accs.n_bad() == 0 and accs.n() > 0
            entry["variants"][variant] = v
        entry["pass"] = all(v["pass"] for v in entry["variants"].values())
        if "waste_index" in entry["kinds"]:
            entry["note"] = ("the bundled table's responses use kind waste_index: the index is 1000 sum(activity / "
                             "A2) per kg of material, not the `a2` kind's per-gram sum; recomputed accordingly")
        out["subchecks"]["bundled_table"] = entry
    verdicts = [v["pass"] for v in out["subchecks"].values()]
    out["pass"] = False if False in verdicts else (None if None in verdicts else True)
    if out["pass"] is None:
        out["status"] = "partial: the bundled-table part is not merged"
    out["tolerance_relative"] = A2_REL_TOL
    return out


# ------------------------------------------------------------------------------ G5: independent source parsers


def decimal_to_float(mantissa, exponent):
    return float(decimal.Decimal(mantissa) * decimal.Decimal(10) ** exponent)


CFR_VALUE = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*×\s*10\s*(-?)\s*\^\{\s*(-?\d+)\s*\})?$")


def cfr_table_rows(path):
    """Rows of the first table body in an eCFR section: list of cell-text lists, superscripts as ^{...}."""
    text = Path(path).read_text(encoding="utf-8")
    body = text[text.index("<TBODY"):text.index("</TBODY>")]
    rows = []
    for tr in re.findall(r"<TR>(.*?)</TR>", body, re.S):
        cells = []
        for td in re.findall(r"<TD[^>]*>(.*?)</TD>", tr, re.S):
            td = re.sub(r"<sup>(.*?)</sup>", lambda m: "^{" + re.sub(r"<[^>]+>", "", m.group(1)).strip() + "}", td,
                        flags=re.S)
            td = re.sub(r"<[^>]+>", "", td)
            td = html.unescape(td).replace("−", "-").replace("\xa0", " ")
            cells.append(re.sub(r"\s+", " ", td).strip())
        rows.append(cells)
    return rows


def cfr_value(cell):
    """(mantissa, exponent) or None for 'Unlimited' / cross-references; ValueError for anything else."""
    if cell == "Unlimited" or cell.startswith("see §"):
        return None
    m = CFR_VALUE.match(cell)
    if not m:
        raise ValueError(f"cannot read {cell!r}")
    mantissa, sign, digits = m.groups()
    exponent = 0 if digits is None else int(digits) * (-1 if sign == "-" else 1)
    return mantissa, exponent


def classify_cfr_label(label):
    """('nuclide', key) | ('lung', key, variant) | ('life', base key, short|long) | ('element', text) | ('unknown')."""
    text = re.sub(r"\([a-z]\)", "", label)
    text = re.sub(r"[\s,]+$", "", text).strip()
    m = re.fullmatch(r"([A-Z][a-z]?)-(\d+)(m)?", text)
    if m and m.group(1) in Z_OF:
        return ("nuclide", f"{m.group(1)}{m.group(2)}" + ("m1" if m.group(3) else ""))
    m = re.fullmatch(r"([A-Z][a-z]?)-(\d+)(m)? \((fast|medium|slow|all) lung absorption(?: types)?\)", text)
    if m and m.group(1) in Z_OF:
        return ("lung", f"{m.group(1)}{m.group(2)}" + ("m1" if m.group(3) else ""), m.group(4))
    m = re.fullmatch(r"([A-Z][a-z]?)-(\d+) \((short|long)[ -]lived\)", text)
    if m and m.group(1) in Z_OF:
        return ("life", f"{m.group(1)}{m.group(2)}", m.group(3))
    if re.fullmatch(r"T ?\(H-3\)", text):
        return ("alias", "H3")
    if re.fullmatch(r"([A-Z][a-z]?) ?\((nat|dep|enriched[^)]*)\)", text):
        return ("element", text)
    return ("unknown", text)


def isomer_by_half_life(base_key, which, records):
    """The ACTINV key of the shorter- or longer-lived of the ground state and first isomer, from this script's decay
    parse. None when the file lacks either record."""
    m = NUCLIDE_NAME.match(base_key)
    za = Z_OF[m.group(1)] * 1000 + int(m.group(2))
    states = {liso: records.get((za, liso)) for liso in (0, 1)}
    if not all(states.values()):
        return None
    ordered = sorted(states, key=lambda liso: states[liso]["half_life"])
    pick = ordered[0] if which == "short" else ordered[-1]
    return base_key + (f"m{pick}" if pick else "")


CFR_TABLES = {
    "us-49cfr173-a1.json": ("49CFR173.435.xml", 2, 12, "a1"),
    "us-49cfr173-a2.json": ("49CFR173.435.xml", 4, 12, "a2"),
    "us-49cfr173-exempt-concentration.json": ("49CFR173.436.xml", 2, 3, "exempt-concentration"),
    "us-49cfr173-exempt-consignment.json": ("49CFR173.436.xml", 4, 0, "exempt-consignment"),
}


def cfr_expected(xml_path, column, shift, records):
    """Independent expectation for the three uranium variants: {variant: {key: (value, source row, label, cell)}},
    plus bookkeeping: rows by classification, rows whose isomer mapping could not be checked."""
    expected = {v: {} for v in ("fast", "medium", "slow")}
    stats = {"rows": 0, "unlimited_or_reference": 0, "element_level": 0, "unknown": [], "isomer_unverified": [],
             "special_rows": [], "duplicates": []}
    for index, cells in enumerate(cfr_table_rows(xml_path), start=1):
        stats["rows"] += 1
        label, cell = cells[0], cells[column]
        kind = classify_cfr_label(label)
        if kind[0] == "unknown":
            stats["unknown"].append(label)
            continue
        if kind[0] == "element":
            stats["element_level"] += 1
            continue
        value = cfr_value(cell)
        if value is None:
            stats["unlimited_or_reference"] += 1
            continue
        number = decimal_to_float(value[0], value[1] + shift)
        row = (number, index, label, cell)
        if kind[0] in ("nuclide", "alias"):
            if kind[1] in expected["medium"]:
                stats["duplicates"].append(kind[1])
            for v in expected:
                expected[v][kind[1]] = row
            if kind[0] == "alias":
                stats["special_rows"].append(index)
        elif kind[0] == "lung":
            for v in (expected if kind[2] == "all" else [kind[2]]):
                expected[v][kind[1]] = row
            stats["special_rows"].append(index)
        else:
            key = isomer_by_half_life(kind[1], kind[2], records)
            if key is None:
                stats["isomer_unverified"].append(label)
                continue
            for v in expected:
                expected[v][key] = row
            stats["special_rows"].append(index)
    return expected, stats


EURATOM_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(?:\s*×\s*10\^\{\s*(-?\d+)\s*\})?$")


def euratom_rows(path):
    """(Table A part 1 rows, Table B rows) as cell-text lists, read from the XHTML of Annex VII."""
    text = Path(path).read_text(encoding="utf-8")
    annex = text[text.index('id="anx_VII"'):]
    annex = annex[:annex.index("ANNEX VIII")]
    part1 = annex.index(">TABLE A PART 1<")
    part2 = annex.index(">TABLE A PART 2<")
    table_b = annex.index(">TABLE B<")

    def rows_of(segment):
        rows = []
        for tr in re.findall(r'<tr class="oj-table">(.*?)</tr>', segment, re.S):
            cells = []
            for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S):
                td = re.sub(r'<span class="oj-super">(.*?)</span>', r"^{\1}", td, flags=re.S)
                td = re.sub(r"<[^>]+>", "", td)
                td = html.unescape(td).replace("\xa0", " ").replace("−", "-")
                cells.append(re.sub(r"\s+", " ", td).strip())
            rows.append(cells)
        return rows

    return rows_of(annex[part1:part2]), rows_of(annex[table_b:])


def euratom_number(cell):
    """'100', '0,1', '1 000' (space as thousands separator), '1 × 10^{6}'."""
    text = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", cell.replace(",", ".").strip())
    m = EURATOM_NUMBER.match(text)
    if not m:
        return None
    return m.group(1), int(m.group(2) or 0)


def euratom_key(label):
    """('Ag-110 m (1)') -> ('Ag110m1', '1') ; None when not a nuclide."""
    m = re.fullmatch(r"([A-Z][a-z]?)-(\d+)( ?m)?(?: \((\d)\))?", label.strip())
    if not m or m.group(1) not in Z_OF:
        return None
    return f"{m.group(1)}{m.group(2)}" + ("m1" if m.group(3) else ""), m.group(4)


def euratom_expected(path):
    """{table file: {key: (value, row index, label, cell)}} plus the duplicate-key bookkeeping."""
    part1, table_b = euratom_rows(path)
    out = {"euratom-2013-59-annex-vii-a.json": {}, "euratom-2013-59-annex-vii-b-concentration.json": {},
           "euratom-2013-59-annex-vii-b-activity.json": {}}
    notes = {"duplicates": {}, "rows": {"A1": 0, "B": 0}}

    def put(table, key, marker, number, shift, index, label, cell):
        row = (decimal_to_float(number[0], number[1] + shift), index, label, cell)
        if key in out[table]:
            # a duplicated nuclide (Table B lists U-240 twice): the row without the parent/progeny mark is kept
            notes["duplicates"].setdefault(table, []).append(key)
            if marker is not None:
                return
        out[table][key] = row

    for index, cells in enumerate(part1, start=1):
        if len(cells) == 2 and euratom_key(cells[0]) and euratom_number(cells[1]):
            key, marker = euratom_key(cells[0])
            notes["rows"]["A1"] += 1
            put("euratom-2013-59-annex-vii-a.json", key, marker, euratom_number(cells[1]), 3, index, cells[0],
                cells[1])
    for index, cells in enumerate(table_b, start=1):
        if len(cells) == 3 and euratom_key(cells[0]) and euratom_number(cells[1]) and euratom_number(cells[2]):
            key, marker = euratom_key(cells[0])
            notes["rows"]["B"] += 1
            put("euratom-2013-59-annex-vii-b-concentration.json", key, marker, euratom_number(cells[1]), 3, index,
                cells[0], cells[1])
            put("euratom-2013-59-annex-vii-b-activity.json", key, marker, euratom_number(cells[2]), 0, index,
                cells[0], cells[2])
    return out, notes


def pick_sample(keys, n=30):
    """Evenly spaced positions over the ordered keys, always including the first and last."""
    if len(keys) <= n:
        return list(keys)
    return [keys[round(i * (len(keys) - 1) / (n - 1))] for i in range(n)]


def compare_rows(label, expected, coefficients, special_rows=(), n=30):
    """Full and sampled comparison of one response's coefficients against the independent expectation."""
    keys_in_order = sorted(expected, key=lambda k: expected[k][1])
    sample = pick_sample(keys_in_order, n)
    sample += [k for k in keys_in_order if expected[k][1] in set(special_rows) and k not in sample]
    sampled = []
    for key in sample:
        value, index, source_label, cell = expected[key]
        got = coefficients.get(key)
        sampled.append({"nuclide": key, "source_row": index, "source_label": source_label, "source_cell": cell,
                        "expected": value, "committed": got, "match": got == value})
    full_bad = [{"nuclide": k, "expected": expected[k][0], "committed": coefficients.get(k)}
                for k in keys_in_order if coefficients.get(k) != expected[k][0]]
    extra = sorted(set(coefficients) - set(expected))
    return {"response": label, "coefficients_committed": len(coefficients), "rows_expected": len(expected),
            "sample_size": len(sampled), "sample_mismatches": [s for s in sampled if not s["match"]],
            "full_comparison_mismatches": full_bad[:MAX_LISTED], "full_comparison_n_mismatches": len(full_bad),
            "committed_keys_not_in_source": extra[:MAX_LISTED], "n_committed_keys_not_in_source": len(extra),
            "sample": sampled}


def manifest_hashes(sources_dir):
    out = {}
    for line in (sources_dir / "MANIFEST.sha256").read_text().splitlines():
        m = re.match(r"^([0-9a-f]{64})\s+\./(.+)$", line)
        if m:
            out[m.group(2)] = m.group(1)
    return out


def part_b_status(data_dir, scripts_dir):
    data_files = ["us-49cfr173-a1.json", "us-49cfr173-a2.json", "us-49cfr173-exempt-concentration.json",
                  "us-49cfr173-exempt-consignment.json", "us-49cfr173-435.rows.json", "us-49cfr173-436.rows.json",
                  "euratom-2013-59-annex-vii-a.json", "euratom-2013-59-annex-vii-b-activity.json",
                  "euratom-2013-59-annex-vii-b-concentration.json", "euratom-2013-59-annex-vii.rows.json"]
    script_files = ["convert_cfr_173435.py", "convert_euratom_annex_vii.py", "regulatory_common.py",
                    "compare_fispact.py", "fetch_icrp119.py"]
    missing = [str(data_dir / f) for f in data_files if not (data_dir / f).exists()]
    missing += [str(scripts_dir / f) for f in script_files if not (scripts_dir / f).exists()]
    return not missing, missing, data_files, script_files


def gate_g5(runner, workdir, data_dir, scripts_dir, sources_dir, fispact_dir, decay_primary):
    merged, missing, data_files, _ = part_b_status(data_dir, scripts_dir)
    out = {"data_dir": str(data_dir), "scripts_dir": str(scripts_dir)}
    if not merged:
        out.update({"pass": None, "status": "not_merged", "missing": missing[:MAX_LISTED]})
        return out
    out["status"] = "run"
    scratch = Path(tempfile.mkdtemp(prefix="p123_g5_", dir=str(workdir)))
    manifest = manifest_hashes(sources_dir)
    cfr_dir, eu_xhtml = sources_dir / "us_cfr_49_173435", sources_dir / "eu_euratom" / \
        "Directive_2013_59_Euratom_OJ_L13_2014.xhtml"
    pdf = sources_dir / "icrp" / "P119_JAICRP_41s_front.pdf"
    sources = {
        "49CFR173.435.xml": cfr_dir / "49CFR173.435.xml", "49CFR173.436.xml": cfr_dir / "49CFR173.436.xml",
        eu_xhtml.name: eu_xhtml, pdf.name: pdf}
    pins = {}
    for name, path in sources.items():
        pins[name] = {"path": str(path), "sha256": sha256_file(path),
                      "manifest_sha256": manifest.get(str(path.relative_to(sources_dir)))}
        pins[name]["matches_manifest"] = pins[name]["sha256"] == pins[name]["manifest_sha256"]
    out["source_files"] = pins
    out["source_files_match_manifest"] = all(p["matches_manifest"] for p in pins.values())
    # ---- (a) converters re-run, byte for byte
    converters = {}
    for name, args, produced in (
            ("convert_cfr_173435", [scripts_dir / "convert_cfr_173435.py", cfr_dir, scratch / "cfr"],
             data_files[:6]),
            ("convert_euratom_annex_vii", [scripts_dir / "convert_euratom_annex_vii.py", eu_xhtml, scratch / "eu"],
             data_files[6:])):
        result = runner.run_script(f"g5_{name}", args, ROOT, timeout=900)
        entry = {"exit_code": None if result is None else result[0], "files": {}}
        out_dir = args[-1]
        for fname in produced:
            fresh = out_dir / fname
            committed = data_dir / fname
            entry["files"][fname] = {
                "regenerated": fresh.exists(), "committed_sha256": sha256_file(committed),
                "regenerated_sha256": sha256_file(fresh) if fresh.exists() else None,
                "byte_identical": fresh.exists() and fresh.read_bytes() == committed.read_bytes()}
        extra = sorted(p.name for p in out_dir.glob("*") if p.name not in produced) if out_dir.exists() else []
        entry["unexpected_outputs"] = extra
        entry["pass"] = bool(result and result[0] == 0 and not extra
                             and all(f["byte_identical"] for f in entry["files"].values()))
        converters[name] = entry
    out["converters_byte_identical"] = converters
    # ---- (b) independent source parsing: sample and full comparison
    try:
        needed = {(63150, 0), (63150, 1), (93236, 0), (93236, 1), (73178, 0), (73178, 1)}
        records = load_decay(decay_primary, None, needed) if decay_primary and Path(decay_primary).exists() else {}
    except (OSError, ValueError) as error:
        records = {}
        out["decay_for_isomer_mapping_error"] = f"{type(error).__name__}: {error}"
    out["isomer_mapping_decay_file"] = str(decay_primary) if records else None
    tables = {}
    for fname, (xml, column, shift, table_id) in CFR_TABLES.items():
        expected, stats = cfr_expected(cfr_dir / xml, column, shift, records)
        doc = json.loads((data_dir / fname).read_text())
        by_id = {r["id"]: r for r in doc["responses"]}
        entry = {
            "source": xml, "source_rows": stats["rows"],
            "unlimited_or_cross_reference_rows": stats["unlimited_or_reference"],
            "element_level_rows_not_coefficients": stats["element_level"],
            "unclassified_labels": stats["unknown"], "duplicate_nuclide_rows": stats["duplicates"],
            "isomer_rows_not_independently_mapped": stats["isomer_unverified"], "responses": {},
            "response_ids_as_expected": sorted(by_id) == sorted(f"{table_id}-u-{v}" for v in expected),
            "source_sha256_in_table_matches_file": doc["source"]["source_sha256"] == sha256_file(cfr_dir / xml)}
        for variant, exp in expected.items():
            rid = f"{table_id}-u-{variant}"
            if rid in by_id:
                entry["responses"][rid] = compare_rows(rid, exp, by_id[rid]["coefficients"], stats["special_rows"])
        entry["pass"] = (entry["response_ids_as_expected"] and entry["source_sha256_in_table_matches_file"]
                         and not stats["unknown"] and not stats["isomer_unverified"] and bool(entry["responses"])
                         and not stats["duplicates"]
                         and all(r["sample_size"] >= 25 and not r["sample_mismatches"]
                                 and r["full_comparison_n_mismatches"] == 0
                                 and r["n_committed_keys_not_in_source"] == 0 for r in entry["responses"].values()))
        tables[fname] = entry
    eu_expected, eu_notes = euratom_expected(eu_xhtml)
    for fname, exp in eu_expected.items():
        doc = json.loads((data_dir / fname).read_text())
        response = doc["responses"][0]
        entry = {"source": eu_xhtml.name, "rows_read": eu_notes["rows"], "duplicate_keys_in_source": eu_notes[
            "duplicates"].get(fname, []), "responses": {response["id"]: compare_rows(response["id"], exp,
                                                                                      response["coefficients"])},
                 "source_sha256_in_table_matches_file": doc["source"]["source_sha256"] == sha256_file(eu_xhtml)}
        r = entry["responses"][response["id"]]
        entry["pass"] = (entry["source_sha256_in_table_matches_file"] and r["sample_size"] >= 25
                         and not r["sample_mismatches"] and r["full_comparison_n_mismatches"] == 0
                         and r["n_committed_keys_not_in_source"] == 0)
        tables[fname] = entry
    out["independent_source_comparison"] = tables
    # ---- (c) FISPACT agreement, report only
    fispact = {"report_only": True, "files": {}}
    for kind, table_ref, fname in (("a2", "bundled:us-49cfr173-a2", "a2_2012"),
                                   ("clear", "bundled:euratom-2013-59-annex-vii-a", "clear_2012")):
        path = fispact_dir / fname
        if not path.exists():
            fispact["files"][kind] = {"status": "not_run", "reason": f"{path} not found"}
            continue
        json_out = scratch / f"compare_{kind}.json"
        result = runner.run_script(f"g5_compare_{kind}", [scripts_dir / "compare_fispact.py", kind, table_ref, path,
                                                           "--json", json_out], ROOT, timeout=600)
        if result is None or result[0] != 0 or not json_out.exists():
            fispact["files"][kind] = {"status": "failed", "see": f"runs.g5_compare_{kind}"}
            continue
        fispact["files"][kind] = {"fispact_file": str(path), "fispact_sha256": sha256_file(path), "table": table_ref,
                                  "responses": [{k: r[k] for k in ("response", "compared", "match", "match_rate",
                                                                   "rtol", "mismatches",
                                                                   "only_in_actinv_table",
                                                                   "only_in_fispact_explicit_rows")}
                                                for r in json.loads(json_out.read_text())]}
    out["fispact_agreement"] = fispact
    # ---- (d) ICRP 119 locally
    icrp = {"pdf": str(pdf), "pdf_sha256": sha256_file(pdf), "target": "at least 98 % of FISPACT hazards_2012 "
                                                                         "ICRP72 rows within 2 %"}
    table = scratch / "icrp119.json"
    tracked = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=60).stdout.splitlines()
    icrp["tracked_files_with_icrp119_in_name"] = [f for f in tracked if "icrp119" in f.lower()]
    result = runner.run_script("g5_fetch_icrp119", [scripts_dir / "fetch_icrp119.py", "--pdf", pdf, "--out", table,
                                                    "--fispact-convention", "--retrieved",
                                                    datetime.date(2026, 10, 15).isoformat()], ROOT, timeout=1800)
    haz = fispact_dir / "hazards_2012"
    if result is None or result[0] != 0 or not table.exists():
        icrp["status"] = "failed"
    elif not haz.exists():
        icrp["status"] = "not_run"
        icrp["reason"] = f"{haz} not found"
    else:
        json_out = scratch / "compare_hazards.json"
        cmp = runner.run_script("g5_compare_hazards", [scripts_dir / "compare_fispact.py", "hazards", table, haz,
                                                       "--json", json_out], ROOT, timeout=600)
        if cmp is None or cmp[0] != 0 or not json_out.exists():
            icrp["status"] = "failed"
        else:
            icrp["status"] = "run"
            icrp["fispact_hazards_sha256"] = sha256_file(haz)
            known = {"Ta180": "Ta-180 and Ta-180m ground/isomer conventions are swapped between ICRP 119 and the "
                              "FISPACT row (documented in SOURCES.md)",
                     "Ta180m1": "see Ta180", "U231": "inhalation: FISPACT 4.0E-10 against ICRP 119 Table G.1 5.2E-10, "
                                                    "an ICRP 119 Table 2.2 correction (SOURCES.md)"}
            icrp["comparisons"] = []
            for r in json.loads(json_out.read_text()):
                rows = [dict(m, explanation=known.get(m["nuclide"], "unexplained")) for m in r["mismatches"]]
                icrp["comparisons"].append({
                    "response": r["response"], "compared": r["compared"], "within_2_percent": r["match"],
                    "match_rate": r["match_rate"], "rtol": r["rtol"], "exceptions": rows,
                    "unexplained_exceptions": [m["nuclide"] for m in rows if m["explanation"] == "unexplained"],
                    "pass": r["match_rate"] is not None and r["match_rate"] >= 0.98})
            icrp["pass"] = bool(icrp["comparisons"]) and all(c["pass"] for c in icrp["comparisons"])
            icrp["all_exceptions_explained"] = not any(c["unexplained_exceptions"] for c in icrp["comparisons"])
    icrp.setdefault("pass", False)
    for leftover in (table, table.with_suffix(".variants.json")):
        if leftover.exists():  # the derived table is never kept
            leftover.unlink()
    icrp["derived_table_removed_after_comparison"] = not table.exists()
    out["icrp119"] = icrp
    out["pass"] = bool(all(c["pass"] for c in converters.values()) and all(t["pass"] for t in tables.values())
                       and icrp["pass"] and out["source_files_match_manifest"]
                       and set(icrp["tracked_files_with_icrp119_in_name"]) <= {"scripts/regulatory/fetch_icrp119.py"})
    return out


# ------------------------------------------------------------------------------ driver


def print_summary(record):
    for gate in ("G2", "G3", "G4", "G5", "G6"):
        g = record["gates"].get(gate)
        if g is None:
            continue
        flag = g.get("pass")
        text = "PASS" if flag is True else ("FAIL" if flag is False else "INCOMPLETE")
        extra = g.get("status", "") or g.get("error", "")
        print(f"P123 {gate}: {text}" + (f" ({extra})" if extra and flag is not True else ""))
    for entry in record["gates"].get("G2", {}).get("specs", []):
        print(f"  G2/{entry['name']}: {'identical' if entry['identical'] else 'DIFFERENT or not run'}")
    builder = record["gates"].get("G2", {}).get("builder", {})
    if builder:
        print(f"  G2/builder: {builder['status']}")
    for gate in ("G3", "G4", "G6"):
        for name, sub in (record["gates"].get(gate, {}).get("subchecks") or {}).items():
            if isinstance(sub, dict) and "n_comparisons" in sub:
                print(f"  {gate}/{name}: {sub['n_comparisons']} comparisons, {sub['n_mismatches']} mismatches")
            elif isinstance(sub, dict):
                flag = sub.get("pass")
                print(f"  {gate}/{name}: " + ("PASS" if flag else "FAIL" if flag is False else sub.get("status")))
    g5 = record["gates"].get("G5", {})
    if g5.get("status") == "run":
        print("  G5 converters byte-identical: " + ", ".join(f"{k}={v['pass']}" for k, v in
                                                            g5["converters_byte_identical"].items()))
        print("  G5 independent source comparison: " + ", ".join(
            f"{k}={v['pass']}" for k, v in g5["independent_source_comparison"].items()))
        for kind, f in g5["fispact_agreement"]["files"].items():
            for r in f.get("responses", []):
                print(f"  G5 FISPACT {kind} {r['response']}: {r['match']}/{r['compared']} equal "
                      f"({100 * r['match_rate']:.1f} %, report only)")
        for c in g5["icrp119"].get("comparisons", []):
            print(f"  G5 ICRP 119 {c['response']}: {c['within_2_percent']}/{c['compared']} within 2 % "
                  f"({100 * c['match_rate']:.2f} %)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", help="binary built from the P123 base commit (needed for G2)")
    ap.add_argument("--candidate", help="binary built from the P123 implementation commit (G2, G3, G4, G6)")
    ap.add_argument("--workdir", default="target/p123-work")
    ap.add_argument("--out", default="results/p123_radiological.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing run outputs in the workdir")
    ap.add_argument("--gates", default="G2,G3,G4,G5,G6", help="comma-separated gates to run")
    ap.add_argument("--nist-cache", default=str(DEFAULT_NIST_CACHE))
    ap.add_argument("--photon-response", default=str(DEFAULT_PHOTON_RESPONSE),
                    help="the installed photon response the specs name (dry air + Fe)")
    ap.add_argument("--photon-response-all", default=str(DEFAULT_PHOTON_RESPONSE_ALL))
    ap.add_argument("--regulatory-sources", default=str(DEFAULT_SOURCES))
    ap.add_argument("--fispact-dir", default=str(DEFAULT_FISPACT))
    ap.add_argument("--regulatory-data-dir", default=str(DATA_DIR), help="bundled tables (default: this tree)")
    ap.add_argument("--regulatory-scripts-dir", default=str(SCRIPTS_DIR), help="converters (default: this tree)")
    args = ap.parse_args(argv)
    gates = [g.strip().upper() for g in args.gates.split(",") if g.strip()]
    if {"G2"} & set(gates) and not (args.reference and args.candidate):
        ap.error("G2 needs --reference and --candidate")
    if {"G3", "G4", "G6"} & set(gates) and not args.candidate:
        ap.error("G3, G4 and G6 need --candidate")
    # Runs execute from the workdir as well as the repo root, so binaries must be absolute.
    ref_bin = str(Path(args.reference).resolve()) if args.reference else None
    cand_bin = str(Path(args.candidate).resolve()) if args.candidate else None

    workdir = Path(args.workdir)
    if not workdir.is_absolute():
        workdir = ROOT / workdir
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    installed_fe = Path(args.photon_response).expanduser()
    installed_all = Path(args.photon_response_all).expanduser()
    data_dir, scripts_dir = Path(args.regulatory_data_dir), Path(args.regulatory_scripts_dir)
    sources_dir, fispact_dir = Path(args.regulatory_sources).expanduser(), Path(args.fispact_dir).expanduser()

    record = {"protocol": "ACTINV-P123", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "gate_spec": record_file(GATE_SPEC), "base_commit": BASE_COMMIT,
              "gates_requested": gates, "runs": {}, "gates": {}}
    if ref_bin:
        record["reference_binary"] = record_file(ref_bin)
    if cand_bin:
        record["candidate_binary"] = record_file(cand_bin)
    base_spec = json.loads(GATE_SPEC.read_text())
    runner = Runner(workdir, args.timeout, args.reuse, record)
    merged, missing, _, _ = part_b_status(data_dir, scripts_dir)
    record["part_b_merged"] = merged

    built_air = None
    g3_runs = {}
    # ---- G2
    if "G2" in gates:
        g2 = gate_g2(runner, ref_bin, cand_bin, base_spec, workdir, installed_fe, Path(args.nist_cache).expanduser(),
                     installed_all)
        built_air = g2["builder"].get("air_response")
        record["gates"]["G2"] = g2
    elif {"G3", "G4"} & set(gates):
        # the rebuilt air-attenuation response is needed without G2: build it alone
        nist = Path(args.nist_cache).expanduser()
        if (nist / "air.html").exists() and (nist / "z26.html").exists():
            path = workdir / "builder_cand_air.json"
            res = runner.run_script("builder_cand_air", [BUILDER, path, "--air-attenuation", "--cache-dir", nist,
                                                          "--offline"], workdir, timeout=600)
            built_air = str(path) if res and res[0] == 0 else None
    # ---- G3, G4
    if "G3" in gates:
        try:
            g3 = gate_g3(runner, cand_bin, base_spec, workdir, installed_fe, built_air)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            g3 = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        g3_runs = g3.pop("_runs", {})
        record["gates"]["G3"] = g3
    if "G4" in gates:
        try:
            g4 = gate_g4(runner, cand_bin, base_spec, workdir, built_air, g3_runs)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            g4 = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        record["gates"]["G4"] = g4
    # ---- G5
    if "G5" in gates:
        try:
            primary, _fallback = decay_paths(fns_variant(base_spec, "paths"))
            g5 = gate_g5(runner, workdir, data_dir, scripts_dir, sources_dir, fispact_dir, primary)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            g5 = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        record["gates"]["G5"] = g5
    # ---- G6
    if "G6" in gates:
        activity = {}
        for res in (g3_runs.get("g3_built_air_1m"), g3_runs.get("g3_installed_noair")):
            if res:
                for name, act in collect_active([res]).items():
                    activity[name] = max(activity.get(name, 0.0), act)
        if not activity:
            probe = runner.run_spec("g6_activity_probe", cand_bin, runner.write_spec(
                "g6_activity_probe", fns_variant(base_spec, "G6 activity probe")), workdir)
            activity = collect_active([probe]) if probe else {}
        try:
            g6 = gate_g6(runner, cand_bin, base_spec, workdir, activity, merged, data_dir)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            g6 = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        record["gates"]["G6"] = g6

    flags = [record["gates"][g].get("pass") for g in gates if g in record["gates"]]
    if any(f is False for f in flags):
        record["verdict"], code = "FAIL", 1
    elif any(f is None for f in flags) or set(gates) != {"G2", "G3", "G4", "G5", "G6"}:
        record["verdict"], code = "INCOMPLETE", 2
    else:
        record["verdict"], code = "PASS", 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print_summary(record)
    print(f"P123 verdict: {record['verdict']} ({out_path})")
    return code


if __name__ == "__main__":
    sys.exit(main())
