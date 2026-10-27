#!/usr/bin/env python3
"""P152 checker (protocols/ACTINV-P152_PROTOCOL.md): estimated photon spectra with certified dose bounds
(`photon.missing_spectra`) and X-ray exclusion (`photon.exclude_xrays`).

G0, G1 and G7 are external (protocol hash registry; fmt/clippy/tests; CI and committed baselines). They are recorded
as external and do not enter the verdict. This checker evaluates G2-G6. It is written from the protocol and the user
documentation only; it shares no code with the implementation. The decay-record reader and the log-log curve rule
come from check_p123.py (which has its own independent origin); everything specific to P152 is here.

  G2  Defaults unchanged. examples/fns_fe_5min.json, examples/shielding_demo.json and two photon/dose cases with
      unrepresented gamma power (activated steel; activated zinc, each with a point source and an NIST response)
      give byte-identical results from the reference and the candidate apart from `ms`. With `missing_spectra:
      "report"` and `exclude_xrays: false` given explicitly the candidate's results are identical to its default
      results apart from the echo of the request (paths that name the options, and hash fields). The reference
      binary must be the protocol's v1.6.1 release (SHA-256 79e1e860...9c072).
  G3  Estimate exactness, three cases (steel cooling after a day; zinc and nickel, dominated by short-lived
      products). For every estimated nuclide at every step: the set of estimated nuclides equals the set this
      checker derives from its own MT=457 read (activity > 0, E_em > 0, no STYP 0/9 spectrum); E_max (manual and
      capped), the Q cap and the grouped photon numbers and energies of the exponential spectrum n(E) = C
      exp(-a E / E_max), integrated in closed form in 60-digit decimals, equal the candidate's within 1e-12
      relative (a group whose value is below 1e-6 of the nuclide's total is compared to 1e-12 of that total, so
      subtraction noise in a tiny group is not a failure); the energy placed (groups + ungrouped) equals activity x
      E_em within 1e-12; the unrepresented power of the "report" run equals the estimated power plus the
      unrepresented power of the "estimate" run. At least two cases must have estimated nuclides.
  G4  Bounds. rho(E), the dose per unit power in the code's model (contact proxy B/2 mu_a/mu_m, point source at
      1 m), is rebuilt from the response file. Its exact extrema over the allowed intervals of each estimated
      nuclide (found by splitting at every curve knot; log rho is concave on each piece) give lower and upper
      within 1e-9 relative; every estimate lies within its bounds; the estimate equals sum rho(centroid) power over
      the emitted groups; the step totals are represented-only + sum over nuclides for estimate, lower and upper;
      represented-only equals the "report" run's value within 1e-12; the shares are recomputed.
  G5  Whole-library validation with `inspect decay --estimated-spectrum` on the primary decay file: for every
      nuclide with STYP 0 content, E_em > 0 and a branching mode, the evaluated grouped spectrum (contact proxy on
      iron, point source in air at 1 m, per unit power) lies within its bounds, except nuclides whose evaluated
      discrete lines lie above their own largest Q (listed as data inconsistencies); the inspect estimate equals this
      checker's own estimator; accuracy (information): median and 90th percentile of |ln(estimate/evaluated)| with
      and without the Q cap.
  G6  X-ray exclusion in a steel case (Fe-55, Mn-54, Cr-51 emit X-rays): excluded power equals this checker's STYP 9
      power sum within 1e-12; the remaining grouped source equals the full source minus the scaled STYP 9 lines;
      heat, activity and inventory are byte-identical to the run without exclusion.

Overall PASS iff G2-G6 all pass; FAIL exits 1; an incomplete run (gate not requested or not evaluable) exits 2.
Runs are serial, without a shell, each under a timeout (a timed-out child is killed and reaped by subprocess.run).

Usage:
  check_p152.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p152-work]
      [--out results/p152_photon_estimate.json] [--timeout 3600] [--reuse] [--gates G2,G3,G4,G5,G6]
      [--nist-cache ~/.cache/nist-html] [--response PATH]
  check_p152.py --self-check      (offline unit checks; runs no binary)

Needs on disk: actinv-data/v1.1.0/{activation/tendl-2025-neutron-709g.npz, decay/*.dat} (found in this checkout or
an ancestor directory), results/g1_p19_shield_artifact.json (shielding example), ~/.cache/nist-html (offline NIST
pages, to build a response with air attenuation; otherwise ~/nuclear-data/photon-response/nist-xcom-all.json is used
and the point source runs without air attenuation). Run inside the required systemd cgroup.
"""
import argparse
import bisect
import copy
import datetime
import decimal
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p123 as P  # noqa: E402  (ENDF reader, curve rule, runner, diff helpers)

ROOT = Path(__file__).resolve().parents[1]
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
SHIELD_SPEC = ROOT / "examples" / "shielding_demo.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P152_PROTOCOL.md"
BUILDER = ROOT / "scripts" / "build_photon_response.py"
HOME = Path.home()
DEFAULT_RESPONSE_ALL = HOME / "nuclear-data" / "photon-response" / "nist-xcom-all.json"
DEFAULT_NIST_CACHE = HOME / ".cache" / "nist-html"

REFERENCE_PREFIX, REFERENCE_SUFFIX = "79e1e860", "9c072"
EV_J = P.EV_J
AIR_RHO = P.AIR_DENSITY_DEFAULT
EXP_A = 14.0
FIVE_MEV = 5.0e6
SUBTHRESHOLD_TOL = 1e-4
UNGROUPED_TOL = 1e-9
TOL_EXACT = 1e-12
TOL_BOUNDS = 1e-9
FLOOR_FRACTION = 1e-6
POINT_DISTANCE_M = 1.0
POINT_MASS_G = 2.5
MAX_LISTED = 25

decimal.getcontext().prec = 60
Dec = decimal.Decimal


def sha256_file(path):
    return P.sha256_file(path)


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ------------------------------------------------------------------------------ comparison accumulator


class Cmp:
    """Counts comparisons by quantity. `floor` (optional) makes the tolerance relative to max(|expected|, floor)."""

    def __init__(self, tol):
        self.tol = tol
        self.q = {}

    def check(self, quantity, where, got, exp, floor=0.0, tol=None):
        tol = self.tol if tol is None else tol
        e = self.q.setdefault(quantity, {"n": 0, "n_bad": 0, "worst_relative": 0.0, "mismatches": []})
        e["n"] += 1
        if got is None or exp is None or isinstance(got, (bool, str, list, dict)) \
                or isinstance(exp, (bool, str, list, dict)):
            ok = got == exp
        elif not (finite(got) and finite(exp)):
            ok = False
        else:
            scale = max(abs(exp), floor)
            if scale == 0.0:
                ok = got == 0.0
            else:
                err = abs(got - exp) / scale
                ok = err <= tol
                e["worst_relative"] = max(e["worst_relative"], err)
        if not ok:
            e["n_bad"] += 1
            if len(e["mismatches"]) < MAX_LISTED:
                e["mismatches"].append({"where": where, "got": got, "expected": exp})
        return ok

    def n(self):
        return sum(v["n"] for v in self.q.values())

    def n_bad(self):
        return sum(v["n_bad"] for v in self.q.values())

    def summary(self):
        return {"n_comparisons": self.n(), "n_mismatches": self.n_bad(), "tolerance_relative": self.tol,
                "quantities": {k: v for k, v in sorted(self.q.items())}}


# ------------------------------------------------------------------------------ decay records


def parse_record(lines):
    """P123's MF=8/MT=457 parse plus the decay modes with their Q-values: (RTYP, RFS, Q, DQ, BR, DBR)."""
    rec = P.parse_decay_section(lines)
    _head, _values, nxt = P.read_list(lines, 1)
    _mhead, mode_values, _nxt = P.read_list(lines, nxt)
    rec["modes6"] = [tuple(mode_values[i:i + 6]) for i in range(0, len(mode_values) - 5, 6)]
    return rec


def scan_records(path, needed=None):
    """(records keyed by (ZA, LISO), set of every key seen). `needed` None parses every record."""
    records, keys = {}, set()
    state = {"cur": None, "buf": []}

    def flush():
        if state["cur"] is None or not state["buf"]:
            return
        head = P.cont(state["buf"][0])
        key = (int(round(head[0])), head[3])
        keys.add(key)
        if needed is None or key in needed:
            records[key] = parse_record(state["buf"])

    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            if line[70:75] == " 8457":
                mat = line[66:70]
                if state["cur"] != mat:
                    state["cur"], state["buf"] = mat, []
                state["buf"].append(line)
            elif state["cur"] is not None and state["buf"]:
                flush()
                state["cur"], state["buf"] = None, []
        flush()
    return records, keys


def load_records(primary, fallback, needed):
    """Primary wins; the fallback supplies only keys absent from the primary (the run's merge rule)."""
    records, keys = scan_records(primary, needed)
    remaining = needed - set(records) - keys
    if fallback and remaining:
        f_records, _ = scan_records(fallback, remaining)
        records.update(f_records)
    return records


def e_em_of(rec):
    return rec["energies"][2] if len(rec["energies"]) > 2 else 0.0


def e_light_of(rec):
    return rec["energies"][0] if rec["energies"] else 0.0


def has_photon_spectrum(rec):
    return any(s["styp"] in (0, 9) for s in rec["spectra"])


# ------------------------------------------------------------------------------ the estimator (protocol item 1)

CLASSES = ("beta-", "beta+/ec", "it", "other")


def rtyp_class(rtyp):
    """First RTYP digit: 1 beta-, 2 beta+ / EC, 3 IT, else other; RTYP with integer part 10 is other."""
    integer_part = format(rtyp, "g").split(".")[0]
    if integer_part == "10":
        return "other"
    return {"1": "beta-", "2": "beta+/ec", "3": "it"}.get(integer_part[:1], "other")


def dominant_class(modes6):
    sums = {c: 0.0 for c in CLASSES}
    any_positive = False
    for rtyp, _rfs, _q, _dq, br, _dbr in modes6:
        if finite(br) and br > 0.0:
            sums[rtyp_class(rtyp)] += br
            any_positive = True
    if not any_positive:
        return "other"
    best, best_sum = None, -1.0
    for c in CLASSES:  # ties keep the earlier class
        if sums[c] > best_sum:
            best, best_sum = c, sums[c]
    return best


def q_cap_of(modes6):
    qs = [q for _r, _f, q, _d, br, _b in modes6 if finite(br) and br > 0.0 and finite(q)]
    if not qs:
        return None
    q_max = max(qs)
    return q_max if q_max > 0.0 else None


def e_max_manual_of(cls, e_light, e_em):
    if cls == "beta-":
        return 2.0 * e_light if e_light > 0.0 else FIVE_MEV
    if cls == "it":
        return e_em
    return FIVE_MEV


def estimator_inputs(rec, capped=True):
    """Class, manual and effective E_max and the Q cap of a record."""
    cls = dominant_class(rec["modes6"])
    manual = e_max_manual_of(cls, e_light_of(rec), e_em_of(rec))
    q_cap = q_cap_of(rec["modes6"])
    e_max = min(manual, q_cap) if (capped and q_cap is not None) else manual
    return {"class": cls, "e_max_manual": manual, "q_cap": q_cap, "e_max": e_max}


def exp_spectrum(e_em, e_max, a, boundaries):
    """Photons and energy per decay of n(E) = C exp(-a E / e_max) on (0, e_max] with energy e_em, per group of
    `boundaries`, in closed form with 60-digit decimals. Returns lists plus the under/over-structure parts."""
    emax = Dec(e_max)
    k = Dec(a) / emax
    k2 = k * k

    def count_prim(x):
        return -((-k * x).exp()) / k

    def moment_prim(x):
        return -(x / k + 1 / k2) * (-k * x).exp()

    zero = Dec(0)
    total = moment_prim(emax) - moment_prim(zero)
    c = Dec(e_em) / total

    def piece(lo, hi):
        lo_c, hi_c = min(max(lo, zero), emax), min(max(hi, zero), emax)
        if hi_c <= lo_c:
            return 0.0, 0.0
        return float(c * (count_prim(hi_c) - count_prim(lo_c))), float(c * (moment_prim(hi_c) - moment_prim(lo_c)))

    photons, energy = [], []
    for lo, hi in zip(boundaries[:-1], boundaries[1:]):
        n, m = piece(Dec(lo), Dec(hi))
        photons.append(n)
        energy.append(m)
    b0, bn = Dec(boundaries[0]), Dec(boundaries[-1])
    under = piece(zero, b0) if b0 > 0 else (0.0, 0.0)
    over = piece(bn, emax) if emax > bn else (0.0, 0.0)
    all_n, all_e = piece(zero, emax)
    return {"photons": photons, "energy": energy, "under": under, "over": over, "total_photons": all_n,
            "total_energy": all_e}


def expected_nuclide(rec, boundaries, capped=True, a=EXP_A):
    inp = estimator_inputs(rec, capped)
    inp["e_cap"] = inp["q_cap"] if inp["q_cap"] is not None else boundaries[-1]
    inp["e_em"] = e_em_of(rec)
    inp["spectrum"] = exp_spectrum(inp["e_em"], inp["e_max"], a, boundaries)
    return inp


def allowed_intervals(boundaries, e_cap):
    return [(lo, min(hi, e_cap)) for lo, hi in zip(boundaries[:-1], boundaries[1:]) if lo < e_cap]


# ------------------------------------------------------------------------------ rho(E) and its exact extrema


def left_value(curve, e):
    """Limit from below at an absorption edge (first of the equal abscissae); the interpolation rule elsewhere."""
    es, vs = curve
    i = bisect.bisect_left(es, e)
    if i < len(es) and es[i] == e:
        return vs[i]
    return P.curve_value(curve, e)


class RhoModel:
    """Dose per unit power (per W/g) at photon energy E in ACTINV's grouped dose model, rebuilt from the docs.

    contact:  (build_up / 2) (mu_a(E) / mu_mat(E)) 1000 3600                      [Gy/h per W/g]
    point:    mu_a(E) 0.1 exp(-mu(E) rho_air 100 r) / (4 pi r^2) 3600 mass         [Sv/h per W/g]
    mu_mat is the mass-weighted sum of the element mass-attenuation curves. Zero below the response's lowest energy
    (sub-threshold rule); undefined (None) above its highest."""

    def __init__(self, kind, resp, fractions=None, build_up=2.0, r=POINT_DISTANCE_M, air=True, rho_air=AIR_RHO,
                 mass=1.0):
        self.kind = kind
        self.mu_en = resp["mu_en"]
        self.available = True
        self.reason = None
        curves = [self.mu_en]
        self.elements = []
        self.air = False
        if kind == "contact":
            for el, w in sorted(fractions.items()):
                cur = resp["element"].get(el)
                if cur is None:
                    self.available, self.reason = False, f"response lacks {el}"
                    break
                self.elements.append((w, cur))
                curves.append(cur)
            self.K = (build_up / 2.0) * 1000.0 * 3600.0
        else:
            self.air = bool(air)
            self.mu = resp["mu"]
            if self.air and self.mu is None:
                self.available, self.reason = False, "no air_mass_attenuation curve"
            if self.air and self.mu is not None:
                curves.append(self.mu)
            self.cc = rho_air * 100.0 * r if self.air else 0.0
            self.K = 0.1 * 3600.0 * mass / (4.0 * math.pi * r * r)
        self.curves = curves
        self.e_lo = max(c[0][0] for c in curves)
        self.e_hi = min(c[0][-1] for c in curves)
        self._pieces = {}

    def value(self, e):
        if not self.available or e > self.e_hi:
            return None
        if e < self.e_lo:
            return 0.0
        mu_en = P.curve_value(self.mu_en, e)
        if self.kind == "contact":
            mu_mat = 0.0
            for w, cur in self.elements:
                v = P.curve_value(cur, e)
                if v is None:
                    return None
                mu_mat += w * v
            return self.K * mu_en / mu_mat
        tr = 1.0
        if self.air:
            tr = math.exp(-self.cc * P.curve_value(self.mu, e))
        return self.K * mu_en * tr

    def _laws(self, a, b):
        """Per curve, (value at a, log-log slope) on the piece [a, b] between consecutive knots."""
        ln = math.log(b / a)
        out = []
        for cur in self.curves:
            ya, yb = P.curve_value(cur, a), left_value(cur, b)
            out.append((ya, math.log(yb / ya) / ln))
        return out

    def _eval(self, laws, t):
        """(rho, d ln rho / d x) at x = ln a + t for the piece's power laws."""
        ya_en, s_en = laws[0]
        if self.kind == "contact":
            num = ya_en * math.exp(s_en * t)
            den = dsum = 0.0
            for (w, _c), (y, s) in zip(self.elements, laws[1:]):
                term = w * y * math.exp(s * t)
                den += term
                dsum += term * s
            return self.K * num / den, s_en - dsum / den
        val = self.K * ya_en * math.exp(s_en * t)
        g = s_en
        if self.air:
            y, s = laws[1]
            mu_t = y * math.exp(s * t)
            val *= math.exp(-self.cc * mu_t)
            g = s_en - self.cc * s * mu_t
        return val, g

    def piece_extrema(self, a, b):
        """{"closure": (min, max), "naive": (min, max)} on [a, b]. closure uses the limits at both piece ends (the
        supremum / infimum, conservative at an absorption edge); naive uses the model's own value at the ends."""
        key = (a, b)
        if key in self._pieces:
            return self._pieces[key]
        va, vb = self.value(a), self.value(b)
        if a == b:
            res = {"closure": (va, va), "naive": (va, va)}
            self._pieces[key] = res
            return res
        laws = self._laws(a, b)
        xb = math.log(b / a)
        r_a, g_a = self._eval(laws, 0.0)
        r_b, g_b = self._eval(laws, xb)
        interior = -math.inf
        if g_a > 0.0 and g_b < 0.0:
            lo, hi = 0.0, xb
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                if self._eval(laws, mid)[1] > 0.0:
                    lo = mid
                else:
                    hi = mid
            interior = self._eval(laws, 0.5 * (lo + hi))[0]
        vals_end = [v for v in (va, vb) if v is not None]
        res = {"closure": (min([r_a, r_b] + vals_end), max([r_a, r_b, interior] + vals_end)),
               "naive": (min(va, vb), max(va, vb, interior))}
        self._pieces[key] = res
        return res

    def knots(self):
        ks = set()
        for cur in self.curves:
            ks.update(cur[0])
        return sorted(ks)

    def extrema(self, intervals):
        """Exact (min, max) of rho over the union of closed energy intervals, in both end conventions; None when
        rho is undefined somewhere (above the response's highest energy, or unavailable)."""
        if not self.available:
            return None
        ks = self.knots()
        res = {"closure": [math.inf, -math.inf], "naive": [math.inf, -math.inf]}
        zero_seen = False
        for lo, hi in intervals:
            if hi > self.e_hi:
                return None
            if lo < self.e_lo:
                zero_seen = True
                lo = self.e_lo
                if hi < lo:
                    continue
            cuts = [lo] + [k for k in ks if lo < k < hi] + ([hi] if hi > lo else [])
            if len(cuts) == 1:
                cuts = [lo, lo]
            for a, b in zip(cuts[:-1], cuts[1:]):
                pe = self.piece_extrema(a, b)
                for conv in ("closure", "naive"):
                    res[conv][0] = min(res[conv][0], pe[conv][0])
                    res[conv][1] = max(res[conv][1], pe[conv][1])
        out = {}
        for conv, (mn, mx) in res.items():
            if mn == math.inf:
                mn = mx = 0.0
            if zero_seen:
                mn = 0.0
            out[conv] = (mn, mx)
        return out


def load_response(path):
    doc = json.loads(Path(path).read_text())
    air = doc["air_mass_energy_absorption"]
    mu = doc.get("air_mass_attenuation")
    return {"path": str(path), "sha256": sha256_file(path),
            "mu_en": (air["energy_eV"], air["values_cm2_g"]),
            "mu": (mu["energy_eV"], mu["values_cm2_g"]) if mu else None,
            "element": {el: (c["energy_eV"], c["values_cm2_g"]) for el, c in doc["element_mass_attenuation"].items()}}


def element_fractions(composition):
    """Mass fractions from element-symbol keys (`FE`, `CR`) of a wt_percent composition."""
    total = float(sum(composition.values()))
    return {k.capitalize(): v / total for k, v in composition.items()}


def group_dose(model, groups, power_total):
    """Dose from grouped power at group centroids. Returns (value or None, sub-threshold power). None when the
    response lacks a value, or the sub-threshold power exceeds 1e-4 of `power_total`."""
    if not model.available:
        return None, 0.0
    total, sub = 0.0, 0.0
    for g in groups:
        p = g["power_W_g"]
        if p <= 0.0:
            continue
        e = g["centroid_eV"]
        v = model.value(e)
        if v is None:
            return None, sub
        if v == 0.0 and e < model.e_lo:
            sub += p
            continue
        total += v * p
    if sub > SUBTHRESHOLD_TOL * power_total:
        return None, sub
    return total, sub


# ------------------------------------------------------------------------------ cases


def find_data_root():
    for cand in [ROOT, *ROOT.parents]:
        if (cand / "actinv-data" / "v1.1.0" / "decay" / "endf-b-viii-0_decay.dat").exists():
            return cand
    return ROOT


DATA_ROOT = find_data_root()

STEEL = {"FE": 70.0, "CR": 18.0, "NI": 10.0, "MN": 2.0}
CASES = {
    # activated steel cooling after a day (Fe-55, Mn-54, Cr-51 grow in; Co-62m from Ni-62(n,p) is estimated)
    "steel": {"composition": STEEL, "schedule": [("86400 s", 1.0), ("60 s", 0.0), ("600 s", 0.0), ("3600 s", 0.0),
                                                   ("86400 s", 0.0), ("2592000 s", 0.0), ("31557600 s", 0.0)]},
    # dominated by short-lived products: Cu-68m, Cu-70 and Cu-70m from Zn(n,p); minutes of cooling
    "zinc": {"composition": {"ZN": 100.0}, "schedule": [("300 s", 1.0), ("10 s", 0.0), ("60 s", 0.0), ("300 s", 0.0),
                                                        ("3600 s", 0.0), ("21600 s", 0.0)]},
    "nickel": {"composition": {"NI": 100.0}, "schedule": [("300 s", 1.0), ("10 s", 0.0), ("60 s", 0.0),
                                                           ("300 s", 0.0), ("3600 s", 0.0)]},
}


def absolutize(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("path", "primary", "fallback", "overrides", "file") and isinstance(v, str) and v \
                    and not v.startswith(("catalog:", "bundled:")) and not Path(v).is_absolute() \
                    and (DATA_ROOT / v).exists():
                node[k] = str((DATA_ROOT / v).resolve())
            else:
                absolutize(v)
    elif isinstance(node, list):
        for v in node:
            absolutize(v)


def build_case_spec(base, name, photon):
    c = CASES[name]
    spec = copy.deepcopy(base)
    absolutize(spec)
    spec["material"]["composition"] = dict(c["composition"])
    spec["material"]["mass_g"] = POINT_MASS_G
    spec["schedule"] = [{"dt": dt, "flux": flux} for dt, flux in c["schedule"]]
    spec["photon"] = photon
    spec["title"] = f"P152 case {name}"
    return spec


def photon_block(resp, air, **extra):
    block = {"response": {"path": resp["path"], "sha256": resp["sha256"]},
             "point_source": {"distance_m": POINT_DISTANCE_M, "air_attenuation": air}}
    block.update(extra)
    return block


def decay_paths_of(spec):
    d = spec["decay"]
    return d["primary"], d.get("fallback") or None


# ------------------------------------------------------------------------------ comparisons of run outputs

ECHO_PATH = re.compile(r"(photon_options|missing_spectra|exclude_xrays|request|sha256|hash|digest)", re.I)


def all_differences(a, b, path="", out=None, limit=400):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in only one result")
            else:
                all_differences(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            all_differences(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


def compare_echo_only(path_a, path_b):
    a, b = json.loads(Path(path_a).read_text()), json.loads(Path(path_b).read_text())
    for r in (a, b):
        r.pop("ms", None)
    diffs = all_differences(a, b)
    other = [d for d in diffs if not ECHO_PATH.search(d.split(":")[0])]
    return {"identical_apart_from_echo": not other, "n_differences": len(diffs),
            "echo_differences": [d for d in diffs if d not in other][:MAX_LISTED],
            "other_differences": other[:MAX_LISTED]}


# ------------------------------------------------------------------------------ G3 and G4 per case


def estimated_entries(step):
    es = step["photon_source"].get("estimated_spectra")
    return (es or {}).get("by_nuclide") or []


def active_names(step):
    return {n: a for n, a in step["activity_Bq_per_g"].items() if finite(a) and a > 0.0}


def analyze_case(name, spec, rep, est, models, records, g3, g4, info):
    """Every step of one case: G3 checks into `g3`, G4 checks into `g4`."""
    mass = spec["material"]["mass_g"]
    class_map = {}
    n_nuclide_steps = 0
    est_names_seen = set()
    non_canonical, no_record = set(), set()
    for i, (sr, se) in enumerate(zip(rep["steps"], est["steps"])):
        tag = f"{name}/step{i}"
        psr, pse = sr["photon_source"], se["photon_source"]
        bounds = pse["boundaries_eV"]
        entries = estimated_entries(se)
        by_name = {e["nuclide"]: e for e in entries}
        # ---- the set of estimated nuclides, from this checker's own read
        expected_set = {}
        for nuc in active_names(se):
            try:
                key = P.key_of(nuc)
            except ValueError:
                non_canonical.add(nuc)
                continue
            rec = records.get(key)
            if rec is None:
                no_record.add(nuc)
                continue
            if e_em_of(rec) > 0.0 and not has_photon_spectrum(rec):
                expected_set[nuc] = rec
        g3.check("estimated_set_equals_independent_set", tag, sorted(by_name), sorted(expected_set))
        unrep_expect = sum(se["activity_Bq_per_g"][n] * e_em_of(r) * EV_J for n, r in expected_set.items())
        est_power = pse.get("estimated_gamma_power_W_g")
        src_floor = 1e-12 * max(psr["source_power_W_g"], 1e-300)
        g3.check("estimated_power_equals_independent_sum", tag, est_power, unrep_expect, floor=src_floor, tol=1e-9)
        g3.check("report_unrepresented_equals_independent_sum", tag, psr["unrepresented_gamma_power_W_g"],
                 unrep_expect, floor=src_floor, tol=1e-9)
        g3.check("report_unrepresented_equals_estimated_plus_remaining", tag, psr["unrepresented_gamma_power_W_g"],
                 (est_power or 0.0) + pse["unrepresented_gamma_power_W_g"], floor=src_floor)
        g3.check("source_power_includes_estimate", tag, pse["source_power_W_g"],
                 psr["source_power_W_g"] + (est_power or 0.0))
        # ---- each estimated nuclide
        n_g = len(bounds) - 1
        sum_power, sum_photons = [0.0] * n_g, [0.0] * n_g
        for n in entries:
            rec = records.get(P.key_of(n["nuclide"]))
            w = f"{tag}/{n['nuclide']}"
            if rec is None or not (e_em_of(rec) > 0.0):
                g3.check("nuclide_has_record_and_e_em", w, False, True)
                continue
            est_names_seen.add(n["nuclide"])
            n_nuclide_steps += 1
            ex = expected_nuclide(rec, bounds)
            act = se["activity_Bq_per_g"][n["nuclide"]]
            class_map.setdefault(n["decay_mode"], set()).add(ex["class"])
            g3.check("activity_Bq_g", w, n["activity_Bq_g"], act)
            g3.check("mean_em_energy_eV_per_decay", w, n["mean_em_energy_eV_per_decay"], ex["e_em"])
            g3.check("e_max_manual_eV", w, n["e_max_manual_eV"], ex["e_max_manual"])
            g3.check("e_max_eV", w, n["e_max_eV"], ex["e_max"])
            g3.check("q_cap_eV", w, n["q_cap_eV"], ex["q_cap"])
            g3.check("e_cap_eV", w, n["e_cap_eV"], ex["e_cap"])
            sp = ex["spectrum"]
            tot_e = sum(sp["energy"]) + sp["under"][1] + sp["over"][1]
            tot_n = sum(sp["photons"]) + sp["under"][0] + sp["over"][0]
            g3.check("spectrum_energy_integrates_to_e_em", w, tot_e, ex["e_em"])
            groups = n["groups"]
            g3.check("group_count", w, len(groups), n_g)
            grouped_W_g = 0.0
            for gi, (grp, pn, pe) in enumerate(zip(groups, sp["photons"], sp["energy"])):
                gw = f"{w}/g{gi}"
                g3.check("group_edges", gw, [grp["low_eV"], grp["high_eV"]], [bounds[gi], bounds[gi + 1]])
                g3.check("group_photons_s_g", gw, grp["photons_s_g"], act * pn, floor=FLOOR_FRACTION * act * tot_n)
                g3.check("group_power_W_g", gw, grp["power_W_g"], act * pe * EV_J,
                         floor=FLOOR_FRACTION * act * ex["e_em"] * EV_J)
                g3.check("group_photons_s", gw, grp["photons_s"], mass * act * pn,
                         floor=FLOOR_FRACTION * mass * act * tot_n)
                g3.check("group_power_W", gw, grp["power_W"], mass * act * pe * EV_J,
                         floor=FLOOR_FRACTION * mass * act * ex["e_em"] * EV_J)
                grouped_W_g += grp["power_W_g"]
                sum_power[gi] += grp["power_W_g"]
                sum_photons[gi] += grp["photons_s_g"]
            booked = act * ex["e_em"] * EV_J
            g3.check("power_W_g_booked", w, n["power_W_g"], booked)
            g3.check("energy_placed_equals_activity_x_e_em", w, grouped_W_g + n["ungrouped_power_W_g"], booked)
            g3.check("ungrouped_power_W_g", w, n["ungrouped_power_W_g"], act * (sp["under"][1] + sp["over"][1]) * EV_J,
                     floor=1e-12 * booked)
            ppd = n["photons_per_decay"]
            ok_total = P.rel_diff(ppd, sp["total_photons"]) <= 1e-12
            ok_grouped = P.rel_diff(ppd, sum(sp["photons"])) <= 1e-12
            g3.check("photons_per_decay_is_total_or_grouped", w, ok_total or ok_grouped, True)
            conv = info.setdefault("photons_per_decay_convention", {"total": 0, "grouped_only": 0})
            conv["total" if ok_total else "grouped_only"] += 1
        # ---- the step's group totals contain the estimated nuclides
        for gi, (gr, ge) in enumerate(zip(psr["groups"], pse["groups"])):
            g3.check("step_group_power_equals_report_plus_estimated", f"{tag}/g{gi}", ge["power_W_g"],
                     gr["power_W_g"] + sum_power[gi], floor=FLOOR_FRACTION * max(abs(ge["power_W_g"]), 1e-300))
            g3.check("step_group_photons_equals_report_plus_estimated", f"{tag}/g{gi}", ge["photons_s_g"],
                     gr["photons_s_g"] + sum_photons[gi],
                     floor=FLOOR_FRACTION * max(abs(ge["photons_s_g"]), 1e-300))
        if entries or pse.get("estimated_spectra"):
            check_bounds_step(tag, sr, se, entries, bounds, records, models, g4, info)
    info["estimated_nuclides_seen"] = sorted(est_names_seen)
    info["estimated_nuclide_steps"] = n_nuclide_steps
    info["decay_mode_label_to_class"] = {k: sorted(v) for k, v in class_map.items()}
    info["non_canonical_activity_names"] = sorted(non_canonical)
    info["activity_names_without_record"] = sorted(no_record)
    g3.check("decay_mode_label_is_a_function_of_class", name,
             all(len(v) == 1 for v in info["decay_mode_label_to_class"].values()), True)
    classes = [v[0] for v in info["decay_mode_label_to_class"].values()]
    g3.check("decay_mode_labels_distinguish_classes", name, len(classes) == len(set(classes)), True)


def nuclide_dose(model, n):
    """Expected estimate of one estimated nuclide from its emitted groups (None: dose model gives null)."""
    power = n["power_W_g"]
    if n["ungrouped_power_W_g"] > UNGROUPED_TOL * power:
        return None
    val, _sub = group_dose(model, n["groups"], power)
    return val


def check_bounds_step(tag, sr, se, entries, bounds, records, models, g4, info):
    pse, psr = se["photon_source"], sr["photon_source"]
    es = pse.get("estimated_spectra")
    g4.check("estimated_spectra_block_present", tag, es is not None, True)
    if es is None:
        return
    g4.check("model_is_named", tag, isinstance(es.get("model"), str) and bool(es.get("model")), True)
    g4.check("exponent_a", tag, es.get("exponent_a"), EXP_A)
    for kind, key in (("contact", "contact_gamma_air_dose_proxy_Gy_h"), ("point", "point_source_gamma_air_dose_Sv_h")):
        model = models[kind]
        blk = es.get(key)
        g4.check(f"{kind}_block_present", tag, blk is not None, True)
        if blk is None:
            continue
        sums = {"estimate": 0.0, "lower": 0.0, "upper": 0.0}
        any_null = {"estimate": False, "lower": False, "upper": False}
        per_nuc = {}
        for n in entries:
            w = f"{tag}/{n['nuclide']}/{kind}"
            rec = records[P.key_of(n["nuclide"])]
            nb = n.get(key)
            g4.check(f"{kind}_nuclide_block_present", w, nb is not None, True)
            if nb is None:
                continue
            exp_est = nuclide_dose(model, n)
            ex = expected_nuclide(rec, bounds)
            ext = model.extrema(allowed_intervals(bounds, ex["e_cap"])) if exp_est is not None else None
            g4.check(f"{kind}_estimate_null_iff_expected_null", w, nb["estimate"] is None, exp_est is None)
            if exp_est is not None and nb["estimate"] is not None:
                g4.check(f"{kind}_estimate_equals_sum_rho_power", w, nb["estimate"], exp_est, tol=TOL_BOUNDS)
            if ext is None:
                g4.check(f"{kind}_bounds_null_iff_expected_null", w, [nb["lower"] is None, nb["upper"] is None],
                         [True, True])
            else:
                p = n["power_W_g"]
                cand = {conv: (p * mn, p * mx) for conv, (mn, mx) in ext.items()}
                matched = []
                if nb["lower"] is not None and nb["upper"] is not None:
                    for conv, (lo_e, up_e) in cand.items():
                        if P.rel_diff(nb["lower"], lo_e) <= TOL_BOUNDS and P.rel_diff(nb["upper"], up_e) <= TOL_BOUNDS:
                            matched.append(conv)
                if matched:
                    g4.check(f"{kind}_bounds_match_independent_extrema", w, True, True)
                    tally = info.setdefault(f"{kind}_bounds_convention_matches", {})
                    tally["+".join(matched)] = tally.get("+".join(matched), 0) + 1
                else:
                    g4.check(f"{kind}_bounds_match_independent_extrema", w,
                             {"lower": nb["lower"], "upper": nb["upper"]},
                             {"closure": cand["closure"], "naive": cand["naive"]})
                if nb["estimate"] is not None and nb["lower"] is not None and nb["upper"] is not None:
                    e = nb["estimate"]
                    g4.check(f"{kind}_estimate_within_bounds", w,
                             nb["lower"] * (1 - 1e-12) <= e <= nb["upper"] * (1 + 1e-12), True)
            per_nuc[n["nuclide"]] = nb
            for fld in sums:
                if nb.get(fld) is None:
                    any_null[fld] = True
                else:
                    sums[fld] += nb[fld]
        # ---- represented-only
        rep_groups = [g for nn in pse["by_nuclide"] for g in nn["groups"]]
        rep_power = sum(nn["source_power_W_g"] for nn in pse["by_nuclide"])
        my_rep, _ = group_dose(model, rep_groups, rep_power)
        ro = blk.get("represented_only")
        if kind == "contact":
            g4.check("contact_represented_only_equals_report_run", tag, ro, psr.get(key), tol=TOL_EXACT)
            g4.check("contact_top_level_unchanged_by_estimate", tag, pse.get(key), psr.get(key), tol=TOL_EXACT)
        if ro is not None:
            g4.check(f"{kind}_represented_only_equals_recomputed", tag, ro, my_rep, tol=TOL_BOUNDS)
        else:
            info.setdefault(f"{kind}_represented_only_null_steps", []).append(tag)
        # ---- step totals
        if entries:
            for fld in sums:
                emitted = blk.get(fld)
                if ro is None or any_null[fld]:
                    g4.check(f"{kind}_step_{fld}_null_when_a_component_is_null", tag, emitted is None, True)
                else:
                    g4.check(f"{kind}_step_{fld}_equals_represented_plus_nuclides", tag, emitted, ro + sums[fld],
                             tol=TOL_BOUNDS)
            if blk.get("estimate") is not None and blk.get("lower") is not None and blk.get("upper") is not None:
                g4.check(f"{kind}_step_estimate_within_bounds", tag,
                         blk["lower"] * (1 - 1e-12) <= blk["estimate"] <= blk["upper"] * (1 + 1e-12), True)
            if blk.get("estimate"):
                g4.check(f"{kind}_estimated_share", tag, blk.get("estimated_share"), sums["estimate"] / blk["estimate"],
                         tol=TOL_BOUNDS)
                for nuc, nb in per_nuc.items():
                    if nb["estimate"] is not None:
                        g4.check(f"{kind}_share_of_step_estimate", f"{tag}/{nuc}", nb["share_of_step_estimate"],
                                 nb["estimate"] / blk["estimate"], tol=TOL_BOUNDS)
        if kind == "point":
            psd = pse.get("point_source_dose") or {}
            if blk.get("estimate") is not None and psd.get("gamma_air_dose_Sv_h") is not None:
                g4.check("point_source_dose_is_the_estimate", tag, psd["gamma_air_dose_Sv_h"], blk["estimate"],
                         tol=TOL_BOUNDS)
            tot, _ = group_dose(model, pse["groups"], pse["source_power_W_g"])
            if psd.get("gamma_air_dose_Sv_h") is not None and tot is not None:
                g4.check("point_source_dose_equals_recomputed_from_total_groups", tag, psd["gamma_air_dose_Sv_h"], tot,
                         tol=TOL_BOUNDS)


# ------------------------------------------------------------------------------ G6 helpers


def xray_expected(rec, boundaries):
    """Scale, scaled STYP 0 and STYP 9 grouped photons/energy per decay and the STYP 9 energy per decay (eV).
    None when the record's photon spectra have a continuum (not integrated here); "none" without lines."""
    ng = len(boundaries) - 1
    cnt = {0: [0.0] * ng, 9: [0.0] * ng}
    mom = {0: [0.0] * ng, 9: [0.0] * ng}
    raw = raw9 = 0.0
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
            raw += y * energy
            if s["styp"] == 9:
                raw9 += y * energy
            g = P.line_group(energy, boundaries)
            if g is not None:
                cnt[s["styp"]][g] += y
                mom[s["styp"]][g] += y * energy
    if not any_line or raw <= 0.0:
        return "none"
    e_em = e_em_of(rec)
    scale = e_em / raw if e_em > 0.0 else 1.0
    return {"scale": scale, "cnt0": [c * scale for c in cnt[0]], "mom0": [m * scale for m in mom[0]],
            "cnt9": [c * scale for c in cnt[9]], "mom9": [m * scale for m in mom[9]],
            "energy9_per_decay": raw9 * scale}


# ------------------------------------------------------------------------------ the run context


class Context:
    def __init__(self, args):
        self.args = args
        wd = Path(args.workdir)
        self.workdir = wd if wd.is_absolute() else ROOT / wd
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.record = {"protocol": "ACTINV-P152", "checker": record_file(Path(__file__).resolve()),
                       "protocol_file": record_file(PROTOCOL) if PROTOCOL.exists() else None,
                       "gate_spec": record_file(GATE_SPEC), "gates_requested": [], "runs": {}, "gates": {},
                       "external_gates": {g: "external (see protocol)" for g in ("G0", "G1", "G7")},
                       "data_root": str(DATA_ROOT)}
        self.runner = P.Runner(self.workdir, args.timeout, args.reuse, self.record)
        self.cwd = self._run_cwd()
        self.ref = str(Path(args.reference).resolve()) if args.reference else None
        self.cand = str(Path(args.candidate).resolve()) if args.candidate else None
        self.base_spec = json.loads(GATE_SPEC.read_text())
        self.resp = None
        self.resp_air = False
        self.results = {}

    def _run_cwd(self):
        """Directory the binaries run in: relative data paths of the example specs resolve here."""
        if (ROOT / "actinv-data").exists() and (ROOT / "results").exists():
            return ROOT
        cwd = self.workdir / "cwd"
        cwd.mkdir(exist_ok=True)
        for name, target in (("actinv-data", DATA_ROOT / "actinv-data"), ("results", ROOT / "results")):
            link = cwd / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(target)
        return cwd

    def prepare_response(self):
        """The response all cases use: every element plus the air attenuation curve (offline NIST build) when
        possible; otherwise the installed all-element file, and the point source runs without air attenuation."""
        if self.resp is not None:
            return
        path, air = None, False
        if self.args.response:
            path = Path(self.args.response).expanduser()
            air = bool(load_response(path)["mu"])
        else:
            nist = Path(self.args.nist_cache).expanduser()
            built = self.workdir / "p152_response_all_air.json"
            if not (self.args.reuse and built.exists()) and (nist / "air.html").exists():
                self.runner.run_script("build_response_all_air",
                                       [BUILDER, built, "--elements", "all", "--air-attenuation", "--cache-dir", nist,
                                        "--offline"], self.workdir, timeout=900)
            if built.exists():
                path, air = built, True
        if path is None:
            path, air = DEFAULT_RESPONSE_ALL, False
        self.resp = load_response(path)
        self.resp_air = air and self.resp["mu"] is not None
        self.record["photon_response"] = {"path": str(path), "sha256": self.resp["sha256"],
                                          "air_attenuation_available": self.resp_air}

    def run(self, label, binary, spec, cwd=None):
        key = (label, binary)
        if key in self.results:
            return self.results[key]
        path = self.runner.write_spec(label, spec) if isinstance(spec, dict) else Path(spec)
        res = self.runner.run_spec(label, binary, path, cwd or self.workdir)
        self.results[key] = res
        return res

    def case_spec(self, name, **extra):
        self.prepare_response()
        return build_case_spec(self.base_spec, name, photon_block(self.resp, self.resp_air, **extra))

    def models(self, name):
        fractions = element_fractions(CASES[name]["composition"])
        return {"contact": RhoModel("contact", self.resp, fractions=fractions),
                "point": RhoModel("point", self.resp, air=self.resp_air, mass=POINT_MASS_G)}

    def path(self, label):
        return self.workdir / f"{label}.json"


# ------------------------------------------------------------------------------ gates


def gate_g2(ctx):
    out = {"cases": [], "reference_pinned": f"{REFERENCE_PREFIX}...{REFERENCE_SUFFIX}"}
    ref_sha = ctx.record["reference_binary"]["sha256"]
    out["reference_is_protocol_release"] = ref_sha.startswith(REFERENCE_PREFIX) and ref_sha.endswith(REFERENCE_SUFFIX)
    ctx.prepare_response()
    items = [("fns_fe_5min", GATE_SPEC, ctx.cwd), ("shielding_demo", SHIELD_SPEC, ctx.cwd)]
    for name in ("steel", "zinc"):
        spec = ctx.case_spec(name)
        items.append((f"photon_{name}", ctx.runner.write_spec(f"g2_photon_{name}", spec), ctx.workdir))
    unrep_steps = {}
    for name, spec_path, cwd in items:
        ref = ctx.run(f"g2_{name}_reference", ctx.ref, spec_path, cwd)
        cand = ctx.run(f"g2_{name}_candidate", ctx.cand, spec_path, cwd)
        entry = {"name": name, "spec": str(spec_path)}
        if ref is None or cand is None:
            entry.update({"identical": False, "note": "a run failed; see runs"})
        else:
            entry.update(P.compare_results(ctx.path(f"g2_{name}_reference"), ctx.path(f"g2_{name}_candidate")))
            if name.startswith("photon_"):
                n_unrep = sum(1 for s in ref["steps"]
                              if (s.get("photon_source") or {}).get("unrepresented_gamma_power_W_g", 0.0) > 0.0)
                unrep_steps[name] = n_unrep
                entry["steps_with_unrepresented_power_in_reference"] = n_unrep
        # the options given explicitly at their defaults, on the candidate only
        explicit = json.loads(Path(spec_path).read_text())
        if not isinstance(explicit.get("photon"), dict):
            explicit["photon"] = {}
        explicit["photon"]["missing_spectra"] = "report"
        explicit["photon"]["exclude_xrays"] = False
        exp_res = ctx.run(f"g2_{name}_explicit", ctx.cand, explicit, cwd)
        if exp_res is None or cand is None:
            entry["explicit_defaults"] = {"identical_apart_from_echo": False, "note": "a run failed; see runs"}
        else:
            entry["explicit_defaults"] = compare_echo_only(ctx.path(f"g2_{name}_candidate"),
                                                           ctx.path(f"g2_{name}_explicit"))
        out["cases"].append(entry)
    out["unrepresented_power_cases"] = unrep_steps
    out["has_unrepresented_power_case"] = any(v > 0 for v in unrep_steps.values())
    out["pass"] = bool(out["reference_is_protocol_release"] and out["has_unrepresented_power_case"]
                       and all(c.get("identical") and c["explicit_defaults"].get("identical_apart_from_echo")
                               for c in out["cases"]))
    return out


def load_case_records(spec, results):
    needed = set()
    for res in results:
        for st in res["steps"]:
            for nuc, act in st["activity_Bq_per_g"].items():
                if finite(act) and act > 0.0:
                    try:
                        needed.add(P.key_of(nuc))
                    except ValueError:
                        pass
    primary, fallback = decay_paths_of(spec)
    return load_records(primary, fallback, needed)


def gate_g3_g4(ctx):
    g3_out, g4_out = {"cases": {}}, {"cases": {}}
    ctx.prepare_response()
    cases_with_estimates = total_steps = 0
    ok3 = ok4 = True
    for name in CASES:
        rep_spec = ctx.case_spec(name)
        est_spec = ctx.case_spec(name, missing_spectra="estimate")
        rep = ctx.run(f"case_{name}_report", ctx.cand, rep_spec)
        est = ctx.run(f"case_{name}_estimate", ctx.cand, est_spec)
        if rep is None or est is None:
            g3_out["cases"][name] = {"pass": False, "error": "a run failed; see runs"}
            g4_out["cases"][name] = {"pass": False, "error": "a run failed; see runs"}
            ok3 = ok4 = False
            continue
        g3, g4, info = Cmp(TOL_EXACT), Cmp(TOL_BOUNDS), {}
        try:
            records = load_case_records(est_spec, (rep, est))
            analyze_case(name, est_spec, rep, est, ctx.models(name), records, g3, g4, info)
        except (KeyError, IndexError, ValueError, TypeError, OSError) as error:
            g3.check("analysis_completed", name, f"{type(error).__name__}: {error}", None)
            g4.check("analysis_completed", name, f"{type(error).__name__}: {error}", None)
        have = info.get("estimated_nuclide_steps", 0) > 0
        cases_with_estimates += 1 if have else 0
        total_steps += info.get("estimated_nuclide_steps", 0)
        s3, s4 = g3.summary(), g4.summary()
        s3.update({"info": info, "steps": len(est["steps"]), "has_estimated_nuclides": have,
                   "pass": g3.n_bad() == 0 and g3.n() > 0})
        s4.update({"steps": len(est["steps"]), "has_estimated_nuclides": have, "pass": g4.n_bad() == 0 and g4.n() > 0})
        s4["ranges_emitted_steps"] = {
            kind: sum(1 for st in est["steps"]
                      if ((st["photon_source"].get("estimated_spectra") or {}).get(key) or {}).get("lower") is not None)
            for kind, key in (("contact", "contact_gamma_air_dose_proxy_Gy_h"),
                              ("point", "point_source_gamma_air_dose_Sv_h"))}
        g3_out["cases"][name], g4_out["cases"][name] = s3, s4
        ok3 = ok3 and s3["pass"]
        ok4 = ok4 and s4["pass"]
    for out, ok in ((g3_out, ok3), (g4_out, ok4)):
        out["cases_with_estimated_nuclides"] = cases_with_estimates
        out["estimated_nuclide_steps_total"] = total_steps
        out["pass"] = bool(ok and cases_with_estimates >= 2)
        if ok and cases_with_estimates < 2:
            out["note"] = "fewer than two cases produced estimated nuclides; choose other materials or schedules"
    return g3_out, g4_out


def run_inspect(ctx, decay_path, out_path):
    cmd = [ctx.cand, "inspect", "decay", str(decay_path), "--estimated-spectrum", "--out", str(out_path)]
    info = {"command": cmd}
    ctx.record["runs"]["g5_inspect"] = info
    if ctx.args.reuse and Path(out_path).exists():
        info["reused"] = True
        return True
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=ctx.args.timeout, cwd=str(ctx.workdir))
    except subprocess.TimeoutExpired:
        info["error"] = "timed out"
        return False
    info["seconds"] = round(time.monotonic() - started, 3)
    info["exit_code"] = proc.returncode
    info["output_tail"] = proc.stdout[-600:].decode("utf-8", errors="replace")
    return proc.returncode == 0 and Path(out_path).exists()


def percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (pos - lo) * (sorted_vals[hi] - sorted_vals[lo])


def spectrum_dose(model, groups, e_em):
    """Dose per unit power of a grouped per-decay spectrum: sum rho(centroid) energy / e_em; None if undefined."""
    total = 0.0
    for g in groups:
        en = g["energy_eV_per_decay"]
        if en <= 0.0:
            continue
        v = model.value(g["centroid_eV"])
        if v is None:
            return None
        total += v * en
    return total / e_em


def outside_energy(spectrum_doc, prefix):
    """Energy per decay the evaluated spectrum places outside the structure (keys `above_/below_structure_*`)."""
    return sum(v for k, v in spectrum_doc.items() if k.startswith(prefix) and "energy" in k and finite(v))


def gate_g5(ctx):
    out = {}
    ctx.prepare_response()
    primary = decay_paths_of(ctx.case_spec("steel"))[0]
    out["decay_file"] = record_file(primary)
    inspect_path = ctx.workdir / "g5_inspect_all.json"
    if not run_inspect(ctx, primary, inspect_path):
        out.update({"pass": False, "error": "inspect failed; see runs"})
        return out
    doc = json.loads(inspect_path.read_text())
    out["inspect_output"] = record_file(inspect_path)
    out["schema"] = doc.get("schema")
    bounds = doc["boundaries_eV"]
    out["group_structure"] = doc.get("group_structure")
    out["exponent_a"] = doc.get("exponent_a")
    records, _keys = scan_records(primary)
    out["records_parsed"] = len(records)
    contact = RhoModel("contact", ctx.resp, fractions={"Fe": 1.0})
    point = RhoModel("point", ctx.resp, air=ctx.resp_air, mass=1.0)
    out["models"] = {"contact": {"material": "iron", "build_up": 2.0, "e_lo_eV": contact.e_lo, "e_hi_eV": contact.e_hi},
                     "point": {"distance_m": POINT_DISTANCE_M, "air_attenuation": ctx.resp_air,
                               "air_density_g_cm3": AIR_RHO, "mass_g": 1.0, "e_lo_eV": point.e_lo,
                               "e_hi_eV": point.e_hi}}
    cmp = Cmp(1e-9)
    cmp_dose = Cmp(1e-9)  # report only: the inspect estimate's dose against this checker's own estimator
    acc_store = {(kind, label): [] for kind in ("contact", "point") for label in ("capped", "uncapped", "inspect")}
    eligible = contained = cap_effective = 0
    inconsistent, continuum_unverified, unexplained, outside = [], [], [], []
    skipped = {}
    for nd in doc["nuclides"]:
        key = (int(nd["za"]), int(nd["liso"]))
        rec = records.get(key)
        est, ev = nd.get("estimate"), nd.get("evaluated")
        if rec is None:
            skipped["no_record"] = skipped.get("no_record", 0) + 1
            continue
        e_em = e_em_of(rec)
        ex = expected_nuclide(rec, bounds) if e_em > 0.0 else None
        if est is not None and ex is not None:
            w = nd["nuclide"]
            cmp.check("inspect_e_max_eV", w, est["e_max_eV"], ex["e_max"], tol=1e-12)
            cmp.check("inspect_e_max_manual_eV", w, est["e_max_manual_eV"], ex["e_max_manual"], tol=1e-12)
            cmp.check("inspect_q_cap_eV", w, est["q_cap_eV"], ex["q_cap"], tol=1e-12)
            cmp.check("inspect_e_cap_eV", w, est["e_cap_eV"], ex["e_cap"], tol=1e-12)
            cmp.check("inspect_allowed_intervals_eV", w, [[float(lo), float(hi)] for lo, hi in est["allowed_intervals_eV"]],
                      [[float(lo), float(hi)] for lo, hi in allowed_intervals(bounds, ex["e_cap"])])
            sp = est["spectrum"]
            for gi, (g, pn, pe) in enumerate(zip(sp["groups"], ex["spectrum"]["photons"], ex["spectrum"]["energy"])):
                cmp.check("inspect_group_photons", f"{w}/g{gi}", g["photons_per_decay"], pn,
                          floor=FLOOR_FRACTION * ex["spectrum"]["total_photons"], tol=1e-9)
                cmp.check("inspect_group_energy_eV", f"{w}/g{gi}", g["energy_eV_per_decay"], pe,
                          floor=FLOOR_FRACTION * e_em, tol=1e-9)
        eligible_here = (est is not None and ev is not None and ex is not None
                         and any(s["styp"] == 0 for s in rec["spectra"])
                         and any(finite(br) and br > 0.0 for _r, _f, _q, _d, br, _b in rec["modes6"]))
        if not eligible_here:
            skipped["not_eligible"] = skipped.get("not_eligible", 0) + 1
            continue
        eligible += 1
        ex_nc = expected_nuclide(rec, bounds, capped=False)
        q_cap = ex["q_cap"]
        lines_above_q = False
        if q_cap is not None:
            for s in rec["spectra"]:
                if s["styp"] in (0, 9):
                    for energy, intensity in s["discrete"]:
                        if s["fd"] * intensity > 0.0 and energy > q_cap * (1 + 1e-12):
                            lines_above_q = True
        has_continuum = any(s["has_continuum"] for s in rec["spectra"] if s["styp"] in (0, 9))
        if q_cap is not None and not lines_above_q:
            # evaluated content (continuum included) in a group whose low edge is at or above the largest Q
            lines_above_q = any(g["energy_eV_per_decay"] > 0.0 and g["low_eV"] >= q_cap for g in ev["groups"])
        if outside_energy(ev, "above_structure") > 1e-9 * e_em or outside_energy(ev, "below_structure") > 1e-9 * e_em:
            outside.append({"nuclide": nd["nuclide"], "note": "evaluated energy outside the group structure"})
        intervals = allowed_intervals(bounds, ex["e_cap"])
        verdicts = []
        for kind, model in (("contact", contact), ("point", point)):
            if not model.available:
                continue
            ext = model.extrema(intervals)
            v_ev = spectrum_dose(model, ev["groups"], e_em)
            v_est = spectrum_dose(model, est["spectrum"]["groups"], e_em)
            if v_ev is None or ext is None or v_est is None:
                outside.append({"nuclide": nd["nuclide"], "kind": kind,
                                "note": "bounds null: " + ("no extrema (above response top or unavailable)" if ext is None
                                                           else "evaluated or estimate dose undefined")})
                continue
            lo = min(ext["closure"][0], ext["naive"][0])
            hi = max(ext["closure"][1], ext["naive"][1])
            ok = lo * (1 - 1e-9) <= v_ev <= hi * (1 + 1e-9)
            verdicts.append(ok)
            if not ok:
                item = {"nuclide": nd["nuclide"], "kind": kind, "evaluated": v_ev, "lower": lo, "upper": hi,
                        "q_cap_eV": q_cap, "e_cap_eV": ex["e_cap"]}
                item["has_continuum"] = has_continuum
                (inconsistent if lines_above_q else unexplained).append(item)
                if has_continuum and not lines_above_q:
                    continuum_unverified.append(item)  # information only: also counted in unexplained
            if lines_above_q:
                continue
            own = {}
            for label, exn in (("capped", ex), ("uncapped", ex_nc)):
                own[label] = spectrum_dose(model, [{"energy_eV_per_decay": e, "centroid_eV": (e / n if n > 0.0 else 0.0)}
                                                   for e, n in zip(exn["spectrum"]["energy"], exn["spectrum"]["photons"])],
                                           e_em)
            if own["capped"] is not None:
                cmp_dose.check(f"inspect_{kind}_estimate_dose_vs_independent", nd["nuclide"], v_est, own["capped"])
            if v_ev > 0.0:
                for label, val in (("capped", own["capped"]), ("uncapped", own["uncapped"]), ("inspect", v_est)):
                    if val is not None and val > 0.0:
                        acc_store[(kind, label)].append(abs(math.log(val / v_ev)))
        if verdicts and all(verdicts):
            contained += 1
        if q_cap is not None and ex["e_max"] < ex["e_max_manual"]:
            cap_effective += 1
    acc = {f"{kind}_{label}": {"n": len(v), "median_abs_ln_ratio": percentile(sorted(v), 0.5),
                               "p90_abs_ln_ratio": percentile(sorted(v), 0.9)}
           for (kind, label), v in acc_store.items()}
    out.update({"eligible_nuclides": eligible, "nuclides_in_file": len(doc["nuclides"]), "skipped": skipped,
                "nuclides_with_all_kinds_contained": contained, "q_cap_changes_e_max": cap_effective,
                "data_inconsistencies_lines_above_q": inconsistent[:300],
                "n_data_inconsistencies": len({i["nuclide"] for i in inconsistent}),
                "continuum_flagged_failures_information": continuum_unverified[:300],
                "n_continuum_unverified": len({i["nuclide"] for i in continuum_unverified}),
                "unexplained_containment_failures": unexplained[:300],
                "n_unexplained": len({i["nuclide"] for i in unexplained}),
                "outside_structure_or_undefined": outside[:100], "n_outside_or_undefined": len(outside),
                "accuracy_information_only": acc, "inspect_agreement": cmp.summary(),
                "inspect_dose_agreement_report_only": cmp_dose.summary()})
    out["pass"] = bool(eligible > 0 and not unexplained and cmp.n_bad() == 0 and cmp.n() > 0
                       and doc.get("exponent_a") == EXP_A)
    return out


def gate_g6(ctx):
    out = {}
    ctx.prepare_response()
    name = "steel"
    plain_spec = ctx.case_spec(name)
    excl_spec = ctx.case_spec(name, exclude_xrays=True)
    both_spec = ctx.case_spec(name, exclude_xrays=True, missing_spectra="estimate")
    est_spec = ctx.case_spec(name, missing_spectra="estimate")
    plain = ctx.run(f"case_{name}_report", ctx.cand, plain_spec)
    excl = ctx.run(f"g6_{name}_exclude", ctx.cand, excl_spec)
    both = ctx.run(f"g6_{name}_exclude_estimate", ctx.cand, both_spec)
    est = ctx.run(f"case_{name}_estimate", ctx.cand, est_spec)
    if plain is None or excl is None or both is None or est is None:
        return {"pass": False, "error": "a run failed; see runs"}
    cmp = Cmp(TOL_EXACT)
    info = {"incomplete_steps": []}
    records = load_case_records(excl_spec, (plain, excl))
    steps_with_xrays = 0
    skipped = {}
    xray_nuclides = set()
    for i, (sp, sx, sb, se) in enumerate(zip(plain["steps"], excl["steps"], both["steps"], est["steps"])):
        tag = f"{name}/step{i}"
        pp, px = sp["photon_source"], sx["photon_source"]
        bounds = pp["boundaries_eV"]
        n_g = len(bounds) - 1
        mine_total = 0.0
        mine_p, mine_e, mine_x = [0.0] * n_g, [0.0] * n_g, [0.0] * n_g
        incomplete = False
        by_x = {n["nuclide"]: n for n in px["by_nuclide"]}
        for n in pp["by_nuclide"]:
            rec = records.get(P.key_of(n["nuclide"]))
            if rec is None:
                incomplete = True
                skipped[n["nuclide"]] = "no record"
                continue
            ex = xray_expected(rec, bounds)
            if ex is None:
                incomplete = True
                skipped[n["nuclide"]] = "continuum spectrum (not integrated here)"
                continue
            if ex == "none":
                continue
            act = n["activity_Bq_g"]
            xr_W = act * ex["energy9_per_decay"] * EV_J
            mine_total += xr_W
            if xr_W > 0.0:
                xray_nuclides.add(n["nuclide"])
            nx = by_x.get(n["nuclide"])
            w = f"{tag}/{n['nuclide']}"
            if nx is not None:
                cmp.check("nuclide_excluded_xray_power_W_g", w, nx.get("excluded_xray_power_W_g"), xr_W,
                          floor=1e-12 * max(n["source_power_W_g"], 1e-300))
                for gi, (g, gp) in enumerate(zip(nx["groups"], n["groups"])):
                    gw = f"{w}/g{gi}"
                    cmp.check("remaining_group_photons_s_g", gw, g["photons_s_g"], act * ex["cnt0"][gi],
                              floor=FLOOR_FRACTION * max(gp["photons_s_g"], 1e-300))
                    cmp.check("remaining_group_power_W_g", gw, g["power_W_g"], act * ex["mom0"][gi] * EV_J,
                              floor=FLOOR_FRACTION * max(gp["power_W_g"], 1e-300))
                    cmp.check("full_minus_scaled_xray_equals_remaining_photons", gw, g["photons_s_g"],
                              gp["photons_s_g"] - act * ex["cnt9"][gi], floor=1e-12 * max(gp["photons_s_g"], 1e-300))
                    cmp.check("full_minus_scaled_xray_equals_remaining_power", gw, g["power_W_g"],
                              gp["power_W_g"] - act * ex["mom9"][gi] * EV_J,
                              floor=1e-12 * max(gp["power_W_g"], 1e-300))
            else:
                cmp.check("absent_nuclide_has_no_remaining_lines", w, sum(ex["cnt0"]) == 0.0, True)
            for gi in range(n_g):
                mine_p[gi] += act * ex["cnt0"][gi]
                mine_e[gi] += act * ex["mom0"][gi] * EV_J
                mine_x[gi] += act * ex["mom9"][gi] * EV_J
        if mine_total > 0.0:
            steps_with_xrays += 1
        floor_src = 1e-12 * max(pp["source_power_W_g"], 1e-300)
        if not incomplete:
            cmp.check("step_excluded_xray_power_W_g", tag, px["excluded_xray_power_W_g"], mine_total, floor=floor_src)
        else:
            info["incomplete_steps"].append(tag)
            cmp.check("step_excluded_xray_power_not_below_independent_sum", tag,
                      px["excluded_xray_power_W_g"] >= mine_total * (1 - 1e-12), True)
        for gi, (gp, gx) in enumerate(zip(pp["groups"], px["groups"])):
            if not incomplete:
                cmp.check("step_group_power_remaining", f"{tag}/g{gi}", gx["power_W_g"], mine_e[gi],
                          floor=FLOOR_FRACTION * max(gp["power_W_g"], 1e-300))
                cmp.check("step_group_photons_remaining", f"{tag}/g{gi}", gx["photons_s_g"], mine_p[gi],
                          floor=FLOOR_FRACTION * max(gp["photons_s_g"], 1e-300))
                cmp.check("step_group_full_minus_xray_power", f"{tag}/g{gi}", gx["power_W_g"],
                          gp["power_W_g"] - mine_x[gi], floor=1e-12 * max(gp["power_W_g"], 1e-300))
        cmp.check("source_power_closure", tag, px["source_power_W_g"], pp["source_power_W_g"] - px["excluded_xray_power_W_g"],
                  floor=floor_src)
        cmp.check("unrepresented_unchanged_by_exclusion", tag, px["unrepresented_gamma_power_W_g"],
                  pp["unrepresented_gamma_power_W_g"])
        cmp.check("plain_run_has_no_excluded_key", tag, "excluded_xray_power_W_g" in pp, False)

        def strip(st):
            return {k: v for k, v in st.items() if k != "photon_source"}

        cmp.check("steps_apart_from_photon_source_byte_identical", tag, json.dumps(strip(sx), sort_keys=True),
                  json.dumps(strip(sp), sort_keys=True))
        for key in sx:
            if key in ("inventory", "activity_Bq_per_g") or key.startswith("heat"):
                cmp.check(f"{key}_byte_identical", tag, json.dumps(sx[key], sort_keys=True),
                          json.dumps(sp.get(key), sort_keys=True))
        pb, pe = sb["photon_source"], se["photon_source"]
        cmp.check("estimated_power_unchanged_by_exclusion", tag, pb.get("estimated_gamma_power_W_g"),
                  pe.get("estimated_gamma_power_W_g"))
        cmp.check("excluded_power_same_with_estimate", tag, pb["excluded_xray_power_W_g"], px["excluded_xray_power_W_g"],
                  floor=floor_src)
    names = sorted(xray_nuclides)
    emitters = sorted({"Fe55", "Mn54", "Cr51"} & set(names))
    info.update({"nuclides_with_xray_power": names, "steps_with_xray_power": steps_with_xrays,
                 "nuclides_not_recomputed": dict(sorted(skipped.items())), "strong_emitters_found": emitters})
    out.update(cmp.summary())
    out["info"] = info
    out["pass"] = bool(cmp.n_bad() == 0 and cmp.n() > 0 and steps_with_xrays > 0 and emitters)
    return out


# ------------------------------------------------------------------------------ self-check


def synthetic_response():
    """Smooth curves; the element curve has one absorption edge (repeated abscissa)."""
    e = [1.0e3, 2.0e3, 5.0e3, 1.0e4, 3.0e4, 1.0e5, 3.0e5, 1.0e6, 3.0e6, 1.0e7, 2.0e7]
    mu_en = [3.0e2, 4.0e1, 4.0, 5.0e-1, 5.0e-2, 2.5e-2, 2.8e-2, 2.7e-2, 2.1e-2, 1.7e-2, 1.7e-2]
    mu_air = [3.5e2, 5.0e1, 5.0, 6.0e-1, 1.9e-1, 1.6e-1, 1.2e-1, 6.5e-2, 3.6e-2, 2.1e-2, 1.9e-2]
    ee = [1.0e3, 2.0e3, 7.1e3, 7.1e3, 1.0e4, 3.0e4, 1.0e5, 3.0e5, 1.0e6, 3.0e6, 1.0e7, 2.0e7]
    el = [1.0e3, 2.5e2, 2.0e1, 1.5e2, 1.1e2, 1.7e1, 3.0e-1, 8.0e-2, 5.9e-2, 3.6e-2, 2.7e-2, 2.6e-2]
    return {"path": "synthetic", "sha256": "0", "mu_en": (e, mu_en), "mu": (e, mu_air), "element": {"Fe": (ee, el)}}


def self_check():
    failures = []

    def expect(name, cond, detail=""):
        if not cond:
            failures.append(f"{name}: {detail}")

    # 1. closed form against composite Gauss-Legendre quadrature
    nodes = [(-0.9061798459386640, 0.2369268850561891), (-0.5384693101056831, 0.4786286704993665),
             (0.0, 0.5688888888888889), (0.5384693101056831, 0.4786286704993665),
             (0.9061798459386640, 0.2369268850561891)]

    def quad(f, lo, hi, panels=400):
        h = (hi - lo) / panels
        s = 0.0
        for p in range(panels):
            a = lo + p * h
            for x, w in nodes:
                s += w * f(a + 0.5 * h * (x + 1.0)) * 0.5 * h
        return s

    bounds = [0.0, 1.0e4, 1.0e5, 2.0e5, 1.0e6, 3.0e6, 2.0e7]
    for e_em, e_max in ((7.0e5, 2.5e6), (1.0e5, 1.0e5), (3.0e6, 5.0e6), (2.0e6, 8.0e6)):
        sp = exp_spectrum(e_em, e_max, EXP_A, bounds)
        k = EXP_A / e_max
        tot = quad(lambda e: e * math.exp(-k * e), 0.0, e_max)
        c = e_em / tot
        for gi, (lo, hi) in enumerate(zip(bounds[:-1], bounds[1:])):
            lo_c, hi_c = min(lo, e_max), min(hi, e_max)
            n_q = c * quad(lambda e: math.exp(-k * e), lo_c, hi_c) if hi_c > lo_c else 0.0
            en_q = c * quad(lambda e: e * math.exp(-k * e), lo_c, hi_c) if hi_c > lo_c else 0.0
            expect("closed form photons", math.isclose(sp["photons"][gi], n_q, rel_tol=1e-9, abs_tol=1e-15),
                   f"{e_em} {e_max} g{gi}: {sp['photons'][gi]} vs {n_q}")
            expect("closed form energy", math.isclose(sp["energy"][gi], en_q, rel_tol=1e-9, abs_tol=1e-9),
                   f"{e_em} {e_max} g{gi}: {sp['energy'][gi]} vs {en_q}")
        total = sum(sp["energy"]) + sp["under"][1] + sp["over"][1]
        expect("energy conserved", math.isclose(total, e_em, rel_tol=1e-14), f"{total} vs {e_em}")
    sp = exp_spectrum(2.0e6, 5.0e7, EXP_A, [0.0, 1.0e6, 2.0e7])
    expect("energy beyond the top boundary is booked as over",
           sp["over"][1] > 0.0 and math.isclose(sum(sp["energy"]) + sp["over"][1], 2.0e6, rel_tol=1e-14))
    sp = exp_spectrum(2.0e5, 1.0e6, EXP_A, [1.0e4, 1.0e5, 1.0e6])
    expect("energy below the first boundary is booked as under",
           sp["under"][1] > 0.0 and math.isclose(sum(sp["energy"]) + sp["under"][1], 2.0e5, rel_tol=1e-14))

    # 2. mode selection
    def m(*rows):
        return [(r, 0.0, q, 0.0, br, 0.0) for r, q, br in rows]

    expect("class beta-", dominant_class(m((1.0, 1e6, 1.0))) == "beta-")
    expect("class 1.5 is beta-", rtyp_class(1.5) == "beta-")
    expect("class 2.7 is ec", rtyp_class(2.7) == "beta+/ec")
    expect("class 3 is IT", rtyp_class(3.0) == "it")
    expect("class 4 other", rtyp_class(4.0) == "other")
    expect("class 10 other", rtyp_class(10.0) == "other")
    expect("class 0 other", rtyp_class(0.0) == "other")
    expect("largest summed br wins", dominant_class(m((1.0, 1e6, 0.3), (2.0, 1e6, 0.45), (2.7, 1e6, 0.25))) == "beta+/ec")
    expect("two codes of one class add up",
           dominant_class(m((1.0, 1e6, 0.4), (2.0, 1e6, 0.3), (2.7, 1e6, 0.3))) == "beta+/ec"
           and dominant_class(m((1.0, 1e6, 0.6), (2.0, 1e6, 0.2), (2.7, 1e6, 0.2))) == "beta-")
    expect("tie goes to beta-", dominant_class(m((3.0, 1e6, 0.5), (1.0, 1e6, 0.5))) == "beta-")
    expect("tie beta+ over IT", dominant_class(m((3.0, 1e6, 0.5), (2.0, 1e6, 0.5))) == "beta+/ec")
    expect("zero-br modes ignored", dominant_class(m((1.0, 1e6, 0.0), (3.0, 1e6, 0.1))) == "it")
    expect("no positive br is other", dominant_class(m((1.0, 1e6, 0.0))) == "other" and dominant_class([]) == "other")
    expect("E_max beta-", e_max_manual_of("beta-", 3.0e5, 1.0e6) == 6.0e5)
    expect("E_max beta- without light energy", e_max_manual_of("beta-", 0.0, 1.0e6) == FIVE_MEV)
    expect("E_max EC", e_max_manual_of("beta+/ec", 1.0, 2.0e6) == FIVE_MEV)
    expect("E_max IT", e_max_manual_of("it", 1.0, 2.2e5) == 2.2e5)
    expect("E_max other", e_max_manual_of("other", 1.0, 2.2e5) == FIVE_MEV)

    # 3. Q cap
    rec = {"modes6": m((1.0, 8.0e5, 0.9), (2.0, 3.0e6, 0.1)), "energies": [4.0e5, 0, 7.0e5, 0, 0, 0], "spectra": []}
    inp = estimator_inputs(rec)
    expect("largest Q among positive-br modes, cap not binding", inp["q_cap"] == 3.0e6 and inp["e_max_manual"] == 8.0e5
           and inp["e_max"] == 8.0e5, str(inp))
    rec2 = {"modes6": m((1.0, 8.0e5, 0.9)), "energies": [1.0e6, 0, 7.0e5, 0, 0, 0], "spectra": []}
    inp2 = estimator_inputs(rec2)
    expect("Q cap binds", inp2["q_cap"] == 8.0e5 and inp2["e_max_manual"] == 2.0e6 and inp2["e_max"] == 8.0e5, str(inp2))
    expect("uncapped variant", estimator_inputs(rec2, capped=False)["e_max"] == 2.0e6)
    rec3 = {"modes6": m((1.0, 0.0, 0.9), (2.0, -1.0, 0.1), (3.0, 9.0e9, 0.0)), "energies": [1.0e6, 0, 7.0e5, 0, 0, 0],
            "spectra": []}
    expect("no positive Q from positive-br modes means no cap",
           q_cap_of(rec3["modes6"]) is None and estimator_inputs(rec3)["e_max"] == 2.0e6)
    expect("allowed intervals clip at the cap", allowed_intervals(bounds, 1.5e5) == [(0.0, 1.0e4), (1.0e4, 1.0e5),
                                                                                  (1.0e5, 1.5e5)])

    # 4. extremum search against a dense scan, and the edge convention
    resp = synthetic_response()
    no_edge = dict(resp)
    no_edge["element"] = {"Fe": ([1.0e3, 2.0e3, 1.0e4, 3.0e4, 1.0e5, 3.0e5, 1.0e6, 3.0e6, 1.0e7, 2.0e7],
                                 [1.0e3, 2.5e2, 1.1e2, 1.7e1, 3.0e-1, 8.0e-2, 5.9e-2, 3.6e-2, 2.7e-2, 2.6e-2])}
    state = [12345]

    def rnd():
        state[0] = (1103515245 * state[0] + 12345) % (2 ** 31)
        return state[0] / 2 ** 31

    n_scan = 6000
    for kind in ("contact", "point_air", "point_noair"):
        for label, resp_use in (("no_edge", no_edge), ("edge", resp)):
            if kind == "contact":
                model = RhoModel("contact", resp_use, fractions={"Fe": 1.0})
            else:
                model = RhoModel("point", resp_use, air=(kind == "point_air"), r=1.0, mass=2.0)
            for _ in range(12):
                lo = math.exp(math.log(500.0) + rnd() * math.log(1.5e7 / 500.0))
                hi = min(lo * math.exp(rnd() * 3.0), 2.0e7)
                ext = model.extrema([(lo, hi)])
                expect("extrema defined", ext is not None, f"{kind} {lo} {hi}")
                if ext is None or hi <= lo:
                    continue
                vals = [model.value(lo * math.exp(math.log(hi / lo) * t / n_scan)) for t in range(n_scan + 1)]
                dmin, dmax = min(vals), max(vals)
                cl_min, cl_max = ext["closure"]
                expect("closure contains the scan", cl_min <= dmin * (1 + 1e-12) and cl_max >= dmax * (1 - 1e-12),
                       f"{label} {kind} [{lo:.4g},{hi:.4g}] closure {ext['closure']} scan {(dmin, dmax)}")
                # exact extremum: attained at a knot or an end (log rho is concave per piece), so compare with
                # the model's own values there, and with the scan one-sidedly and to 1e-3
                cand = [model.value(lo), model.value(hi)] + [model.value(kk) for kk in model.knots() if lo < kk < hi]
                if label == "no_edge":
                    lo_e = max(lo, model.e_lo)
                    zero = lo < model.e_lo
                    cand = [model.value(lo_e), model.value(hi)] + [model.value(kk) for kk in model.knots()
                                                                    if lo_e < kk < hi]
                    exp_min = 0.0 if zero else min(cand)
                    expect("min attained at an end or knot", math.isclose(cl_min, exp_min, rel_tol=1e-12, abs_tol=1e-300),
                           f"{kind} [{lo:.4g},{hi:.4g}] {cl_min} vs {exp_min}")
                    expect("max at least the scan, close to it", cl_max >= dmax * (1 - 1e-12) and cl_max <= dmax * 1.001,
                           f"{kind} [{lo:.4g},{hi:.4g}] {cl_max} vs {dmax}")
                    expect("min at most the scan, close to it", cl_min <= dmin * (1 + 1e-12) + 1e-300
                           and (dmin == 0.0 or cl_min >= dmin * 0.999), f"{kind} {cl_min} vs {dmin}")
                    expect("closure equals naive without an edge",
                           math.isclose(ext["closure"][1], ext["naive"][1], rel_tol=1e-12)
                           and math.isclose(ext["closure"][0], ext["naive"][0], rel_tol=1e-12, abs_tol=1e-300))
    mc = RhoModel("contact", resp, fractions={"Fe": 1.0})
    ext = mc.extrema([(5.0e3, 7.1e3)])
    expect("edge: the closure contains every value the model attains",
           ext["closure"][0] <= ext["naive"][0] * (1 + 1e-12) and ext["closure"][1] >= ext["naive"][1] * (1 - 1e-12), str(ext))
    left = mc._laws(5.0e3, 7.1e3)
    expect("edge: pre-edge limit is inside the closure", ext["closure"][0] <= mc._eval(left, math.log(7.1e3 / 5.0e3))[0]
           * (1 + 1e-12) <= ext["closure"][1] * (1 + 2e-12), str(ext))
    ext0 = mc.extrema([(0.0, 500.0)])
    expect("all below threshold is zero", ext0["closure"] == (0.0, 0.0), str(ext0))
    ext1 = mc.extrema([(0.0, 1.0e5)])
    expect("straddling the threshold has minimum 0", ext1["closure"][0] == 0.0 and ext1["closure"][1] > 0.0)
    expect("above the response top is undefined", mc.extrema([(1.0e7, 3.0e7)]) is None)
    # an interior stationary maximum of the point source: mu_a ~ E, mu ~ E^2 (unphysical, but a clean test)
    peak = {"path": "s", "sha256": "0", "mu_en": ([1.0e3, 1.0e6], [1.0, 1.0e3]), "mu": ([1.0e3, 1.0e6], [1.0e-3, 1.0e3]),
            "element": {}}
    pm = RhoModel("point", peak, air=True, r=1.0, mass=1.0)
    ext = pm.extrema([(1.0e3, 1.0e6)])
    dense = max(pm.value(1.0e3 * math.exp(math.log(1.0e3) * t / 40000.0)) for t in range(40001))
    expect("interior stationary maximum of the point source",
           math.isclose(ext["closure"][1], dense, rel_tol=1e-6) and ext["closure"][1] >= dense * (1 - 1e-14),
           f"{ext} {dense}")
    # formula spot values
    v = mc.value(1.0e5)
    mu_en = P.curve_value(resp["mu_en"], 1.0e5)
    mu_fe = P.curve_value(resp["element"]["Fe"], 1.0e5)
    expect("contact formula", math.isclose(v, mu_en / mu_fe * 1000.0 * 3600.0, rel_tol=1e-14))
    mp = RhoModel("point", resp, air=True, r=2.0, mass=3.0)
    vp = mp.value(1.0e5)
    expect("point formula", math.isclose(
        vp, mu_en * 0.1 * math.exp(-P.curve_value(resp["mu"], 1.0e5) * AIR_RHO * 100.0 * 2.0) / (4 * math.pi * 4.0)
        * 3600.0 * 3.0, rel_tol=1e-14))

    # 5. X-ray arithmetic on a synthetic record
    spectra = [{"styp": 0, "lcon": 0, "fd": 1.0, "discrete": [(1.0e5, 0.5), (4.0e5, 0.25)], "has_continuum": False,
                "average_energy": 0.0},
               {"styp": 9, "lcon": 0, "fd": 2.0, "discrete": [(6.0e3, 0.1)], "has_continuum": False,
                "average_energy": 0.0}]
    rec = {"energies": [0.0, 0.0, 2.0e5, 0.0, 0.0, 0.0], "spectra": spectra, "modes6": []}
    xb = [0.0, 1.0e4, 1.0e5, 1.0e6]
    xe = xray_expected(rec, xb)
    raw = 0.5e5 + 0.25 * 4.0e5 + 2.0 * 0.1 * 6.0e3
    scale = 2.0e5 / raw
    expect("x-ray scale", math.isclose(xe["scale"], scale, rel_tol=1e-14))
    expect("x-ray energy removed", math.isclose(xe["energy9_per_decay"], 2.0 * 0.1 * 6.0e3 * scale, rel_tol=1e-14))
    expect("x-ray grouped", math.isclose(xe["mom9"][0], 2.0 * 0.1 * 6.0e3 * scale, rel_tol=1e-14) and xe["mom9"][1] == 0.0)
    expect("remaining + x-ray = full", math.isclose(sum(xe["mom0"]) + sum(xe["mom9"]), 2.0e5, rel_tol=1e-14))
    only9 = {"energies": [0.0, 0.0, 1.0e3, 0, 0, 0], "spectra": [spectra[1]], "modes6": []}
    xo = xray_expected(only9, xb)
    expect("x-ray-only nuclide has no remaining source", sum(xo["mom0"]) == 0.0 and sum(xo["cnt0"]) == 0.0)
    cont = {"energies": [0.0, 0.0, 1.0e3, 0, 0, 0], "spectra": [dict(spectra[0], has_continuum=True)], "modes6": []}
    expect("a continuum record is not recomputed", xray_expected(cont, xb) is None)

    # 6. group_dose and the sub-threshold rule
    groups = [{"power_W_g": 1.0e-9, "centroid_eV": 500.0}, {"power_W_g": 1.0, "centroid_eV": 1.0e5}]
    val, sub = group_dose(mc, groups, 1.0 + 1.0e-9)
    expect("sub-threshold share below 1e-4 keeps the value",
           val is not None and math.isclose(val, mc.value(1.0e5), rel_tol=1e-14) and sub == 1.0e-9)
    groups[0]["power_W_g"] = 1.0e-3
    expect("sub-threshold share above 1e-4 gives null", group_dose(mc, groups, 1.001)[0] is None)
    expect("above the response top gives null",
           group_dose(mc, [{"power_W_g": 1.0, "centroid_eV": 3.0e7}], 1.0)[0] is None)
    expect("percentile", percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5) == 3.0
           and math.isclose(percentile([0.0, 10.0], 0.9), 9.0))

    if failures:
        for f in failures:
            print(f"P152 self-check FAIL: {f}")
        return 1
    print("P152 self-check: PASS")
    return 0


# ------------------------------------------------------------------------------ main


def print_summary(record):
    for gate in ("G2", "G3", "G4", "G5", "G6"):
        g = record["gates"].get(gate)
        if g is None:
            continue
        flag = g.get("pass")
        text = "PASS" if flag is True else ("FAIL" if flag is False else "INCOMPLETE")
        print(f"P152 {gate}: {text}" + (f" ({g['error']})" if g.get("error") else "") + (f" ({g['note']})" if g.get("note") else ""))
    g2 = record["gates"].get("G2")
    if g2:
        for c in g2.get("cases", []):
            ex = c.get("explicit_defaults", {})
            print(f"  G2/{c['name']}: {'identical' if c.get('identical') else 'DIFFERENT or not run'}; "
                  f"explicit defaults: {ex.get('identical_apart_from_echo')}")
    for gate in ("G3", "G4"):
        for name, sub in (record["gates"].get(gate, {}).get("cases") or {}).items():
            print(f"  {gate}/{name}: {sub.get('n_comparisons', 0)} comparisons, {sub.get('n_mismatches', '?')} "
                  f"mismatches, estimated nuclide-steps {sub.get('info', {}).get('estimated_nuclide_steps', '-')}")
    g5 = record["gates"].get("G5")
    if g5 and "eligible_nuclides" in g5:
        print(f"  G5: {g5['eligible_nuclides']} eligible nuclides, {g5['n_data_inconsistencies']} data inconsistencies, "
              f"{g5['n_continuum_unverified']} of the failures have continuum content, {g5['n_unexplained']} unexplained")
        for k, v in g5["accuracy_information_only"].items():
            print(f"  G5 accuracy {k}: n={v['n']} median={v['median_abs_ln_ratio']} p90={v['p90_abs_ln_ratio']}")
    g6 = record["gates"].get("G6")
    if g6 and "n_comparisons" in g6:
        print(f"  G6: {g6['n_comparisons']} comparisons, {g6['n_mismatches']} mismatches")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", help="the v1.6.1 release binary (needed for G2)")
    ap.add_argument("--candidate", help="the candidate binary (G2-G6)")
    ap.add_argument("--workdir", default="target/p152-work")
    ap.add_argument("--out", default="results/p152_photon_estimate.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing run outputs in the workdir")
    ap.add_argument("--gates", default="G2,G3,G4,G5,G6")
    ap.add_argument("--nist-cache", default=str(DEFAULT_NIST_CACHE))
    ap.add_argument("--response", help="an all-element photon response (with air_mass_attenuation) to use instead "
                                       "of building one")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    gates = [g.strip().upper() for g in args.gates.split(",") if g.strip()]
    if "G2" in gates and not (args.reference and args.candidate):
        ap.error("G2 needs --reference and --candidate")
    if not args.candidate:
        ap.error("--candidate is required")
    ctx = Context(args)
    ctx.record["gates_requested"] = gates
    ctx.record["candidate_binary"] = record_file(ctx.cand)
    if ctx.ref:
        ctx.record["reference_binary"] = record_file(ctx.ref)
    ctx.record["started_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    decay_primary, decay_fallback = decay_paths_of(build_case_spec(ctx.base_spec, "steel", {}))
    ctx.record["data_files"] = {"decay_primary": record_file(decay_primary)}
    if decay_fallback:
        ctx.record["data_files"]["decay_fallback"] = record_file(decay_fallback)
    lib = ctx.base_spec["library"]
    ctx.record["data_files"]["activation_library"] = {"path": str(DATA_ROOT / lib["path"]),
                                                      "sha256_declared": lib["sha256"]}
    ctx.record["data_files"]["shielding_example"] = record_file(SHIELD_SPEC)

    def guarded(fn):
        try:
            return fn()
        except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError) as error:
            return {"pass": False, "error": f"{type(error).__name__}: {error}"}

    if "G2" in gates:
        ctx.record["gates"]["G2"] = guarded(lambda: gate_g2(ctx))
    if {"G3", "G4"} & set(gates):
        res = guarded(lambda: gate_g3_g4(ctx))
        if isinstance(res, tuple):
            for g, part in zip(("G3", "G4"), res):
                if g in gates:
                    ctx.record["gates"][g] = part
        else:
            for g in ("G3", "G4"):
                if g in gates:
                    ctx.record["gates"][g] = res
    if "G5" in gates:
        ctx.record["gates"]["G5"] = guarded(lambda: gate_g5(ctx))
    if "G6" in gates:
        ctx.record["gates"]["G6"] = guarded(lambda: gate_g6(ctx))

    flags = [ctx.record["gates"][g].get("pass") for g in gates if g in ctx.record["gates"]]
    if any(f is False for f in flags):
        ctx.record["verdict"], code = "FAIL", 1
    elif any(f is None for f in flags) or set(gates) != {"G2", "G3", "G4", "G5", "G6"}:
        ctx.record["verdict"], code = "INCOMPLETE", 2
    else:
        ctx.record["verdict"], code = "PASS", 0
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ctx.record, indent=1, sort_keys=True, default=list) + "\n")
    print_summary(ctx.record)
    print(f"P152 verdict: {ctx.record['verdict']} ({out_path})")
    return code


if __name__ == "__main__":
    sys.exit(main())
