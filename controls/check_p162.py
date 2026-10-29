#!/usr/bin/env python3
"""P162 checker (protocols/ACTINV-P162_PROTOCOL.md): a cited material library and analytic source spectra for new
problems. G0 and G2-G5 are evaluated here; G1 (fmt, check, clippy, tests; `--g1-log` records a log) and G6 (CI on the
pushed evidence commit) are external.

  G0  The protocol's SHA-256 (and any protocols/ACTINV-P162_AMENDMENT_*.md) is in protocols/protocol_hash.txt.
  G1  External, recorded with --g1-log (the log must end with "G1: PASS").
  G2  The library (crates/actinv-core/data/materials-v1.json): schema, symbols in the periodic table, positive values
      summing to 100 within 1e-9, a non-empty citation, and for every entry its basis and composition against
      results/p162/sources.json (each element within 1e-9 of the value derived from the recorded limits or analysis
      by the rule the protocol states; the balance element equal to 100 minus the others). Derivations are this
      script's own.
  G3  Every spectrum recomputed in Python (closed forms; Maxwellian and Watt in 100-digit decimal arithmetic; the 709
      boundaries read from crates/actinv-data/data/fispact_709_groups.json) and compared with the groups
      `actinv new --material ss316 --source SHAPE --flux 1e12` writes: 1e-10 relative on groups above 1e-30 of the
      largest, the total to 1e-12, and adaptive Simpson quadrature of the shape's flux per unit energy to 1e-8 on the
      groups holding 99.9% of the flux (group ratios to the largest group, which needs no normalisation).
  G4  Pb-15.7Li: weight fractions equal 15.7/84.3 atom percent converted with the standard atomic weights of natural
      Li and Pb, to 1e-6.
  G5  `actinv new` then `actinv run` for eurofer97 / fusion-14mev / 1e12 and ss316 / fission / 1e13 on the standard
      data (ACTINV_DATA_DIR, else ./actinv-data under --cwd), as amended (protocols/ACTINV-P162_AMENDMENT_A.md):
      both exit 0; the written problem's material equals the library composition (wt_percent, 1e-9) and the run
      leaves that file unchanged; the result's ledger reads it back (wt_percent, total 100, no unknown element).

Usage: check_p162.py [--actinv BIN] [--g1-log LOG] [--cwd DIR] [--work DIR]   (BIN defaults to $ACTINV_BIN, then
       target/release/actinv)
       check_p162.py --selftest
"""
import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from decimal import Decimal, getcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P162_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
LIBRARY = ROOT / "crates" / "actinv-core" / "data" / "materials-v1.json"
SOURCES = ROOT / "results" / "p162" / "sources.json"
GROUPS = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
RECORD = ROOT / "results" / "p162_library.json"
SCHEMA = "actinv-materials-1"
BASES = {"nominal", "specification_maxima", "measured_heat"}
ENTRY_KEYS = {"id", "name", "category", "composition_wt_percent", "basis", "density_g_cm3", "source", "note"}
OPTIONAL_KEYS = {"atomic_weights_used"}
IDS = ["eurofer97", "eurofer97-3-measured", "ss304", "ss304l", "ss316", "inconel718", "zircaloy4", "al6061",
       "concrete-ordinary", "water", "graphite-reactor", "tungsten", "tantalum", "niobium", "titanium-grade2",
       "copper", "beryllium-s65", "v-4cr-4ti", "pb-15.7li"]
# library id -> id in sources.json
SOURCE_ID = {"eurofer97": "eurofer97", "eurofer97-3-measured": "eurofer97", "ss304": "ss304", "ss304l": "ss304l",
             "ss316": "ss316", "inconel718": "inconel718", "zircaloy4": "zircaloy4", "al6061": "al6061",
             "concrete-ordinary": "concrete_nbs03", "water": "water", "graphite-reactor": "graphite_reactor",
             "tungsten": "w_pure", "tantalum": "tantalum", "niobium": "niobium", "titanium-grade2": "titanium_gr2",
             "copper": "copper_ofhc", "beryllium-s65": "be_s65", "v-4cr-4ti": "v4cr4ti",
             "pb-15.7li": "pbli_eutectic"}
# entries whose density is null although sources.json carries a hint or a secondary value
NULL_DENSITY = {"eurofer97", "eurofer97-3-measured", "v-4cr-4ti", "pb-15.7li"}
SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y "
           "Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re "
           "Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds "
           "Rg Cn Nh Fl Mc Lv Ts Og").split()
LI_WEIGHT, PB_WEIGHT = 6.94, 207.2
O_WEIGHT, BEO_WEIGHT = 15.999, 25.011
TOL_ELEMENT = 1e-9
KT, WATT_A, WATT_B = 0.0253, 0.988e6, 2.249e-6
FUSION_EV = 14.1e6
ONE_OVER_E = (0.5, 1.0e6)
TIMEOUT = 600
CASES = [("eurofer97", "fusion-14mev", 1e12), ("ss316", "fission", 1e13)]
SHAPES = ["fusion-14mev", "fission", "thermal", "one-over-e"]
getcontext().prec = 100


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    paths = [PROTOCOL] + sorted((ROOT / "protocols").glob("ACTINV-P162_AMENDMENT_*.md"))
    out, ok = {}, True
    for path in paths:
        digest = sha256_file(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[path.name] = {"sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    out["pass"] = ok
    return out


def gate_g1(log):
    text = Path(log).read_text(errors="replace") if log and Path(log).is_file() else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    ok = bool(lines) and lines[-1].strip() == "G1: PASS" and "FAILED" not in text
    return {"log": str(log) if log else None, "sha256": sha256_file(log) if text else None, "pass": ok}


# ---------------------------------------------------------------- G2: library against the research record

def numeric(d):
    return {k: float(v) for k, v in d.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def with_balance(parts, key):
    out = dict(parts)
    out[key] = 100.0 - sum(parts.values())
    return out


def expected_composition(lib_id, src):
    """The composition the protocol's rule gives for `lib_id` from its research-record entry `src`, or None."""
    limits = numeric(src.get("impurity_limits_wt_percent", {}))
    if lib_id == "eurofer97":
        majors = {k: float(v) for k, v in src["composition_wt_percent"].items() if k != "Fe"}
        wanted = ["O", "B", "Al", "Si", "P", "Ti", "Co", "Ni", "Cu", "Nb", "Mo"]
        return with_balance({**majors, **{k: limits[k] for k in wanted}}, "Fe")
    if lib_id == "eurofer97-3-measured":
        typical = {}
        for k, v in src["typical_analysis_wt_percent"].items():
            typical[k] = float(v.lstrip("<")) if isinstance(v, str) else float(v)
        return with_balance(typical, "Fe")
    if lib_id == "v-4cr-4ti":
        return with_balance(numeric(src["typical_analysis_wt_percent"]), "V")
    if lib_id == "tungsten":
        return with_balance({k: limits[k] for k in ["C", "N", "Fe", "Ni", "Si"]}, "W")
    if lib_id == "tantalum":
        return with_balance(limits, "Ta")
    if lib_id == "niobium":
        return with_balance({k: v for k, v in limits.items() if "_when_specified" not in k}, "Nb")
    if lib_id == "titanium-grade2":
        return with_balance({k: v for k, v in limits.items() if not k.startswith("residuals")}, "Ti")
    if lib_id == "beryllium-s65":
        parts = {k: v for k, v in limits.items() if k not in ("BeO", "other_metallic")}
        parts["O"] = limits["BeO"] * O_WEIGHT / BEO_WEIGHT
        return with_balance(parts, "Be")
    if lib_id == "pb-15.7li":
        return pbli_weights(LI_WEIGHT, PB_WEIGHT)
    # nominal PNNL entries: as recorded; a sum off 100 beyond 1e-9 is taken up by the largest element
    comp = {k: float(v) for k, v in src["composition_wt_percent"].items()}
    excess = sum(comp.values()) - 100.0
    if abs(excess) > 1e-9:
        big = max(comp, key=comp.get)
        comp[big] -= excess
    return comp


def pbli_weights(li_weight, pb_weight, li_atom=15.7, pb_atom=84.3):
    li = li_atom * li_weight / (li_atom * li_weight + pb_atom * pb_weight) * 100.0
    return {"Li": li, "Pb": 100.0 - li}


def check_entry(entry, src_by_id):
    """Problems found in one library entry (empty when it conforms)."""
    problems = []
    lib_id = entry.get("id")
    keys = set(entry)
    if not ENTRY_KEYS <= keys <= ENTRY_KEYS | OPTIONAL_KEYS:
        problems.append(f"keys {sorted(keys ^ ENTRY_KEYS)}")
    comp = entry.get("composition_wt_percent", {})
    for sym, value in comp.items():
        if sym not in SYMBOLS:
            problems.append(f"symbol {sym!r} is not in the periodic table")
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
            problems.append(f"{sym} is not positive")
    total = sum(v for v in comp.values() if isinstance(v, (int, float)))
    if abs(total - 100.0) > 1e-9:
        problems.append(f"sums to {total!r}")
    source = entry.get("source", {})
    if not (isinstance(source, dict) and set(source) == {"citation", "table", "url"}
            and str(source.get("citation", "")).strip()):
        problems.append("source needs citation, table and url, with a non-empty citation")
    if entry.get("basis") not in BASES:
        problems.append(f"basis {entry.get('basis')!r}")
    if not str(entry.get("note", "")).strip() or not str(entry.get("name", "")).strip():
        problems.append("name and note must be non-empty")
    src = src_by_id.get(SOURCE_ID.get(lib_id))
    if src is None:
        return problems + [f"no research-record entry for {lib_id!r}"]
    want = expected_composition(lib_id, src)
    if set(want) != set(comp):
        problems.append(f"elements differ: library-only {sorted(set(comp) - set(want))}, "
                        f"record-only {sorted(set(want) - set(comp))}")
    for sym in sorted(set(want) & set(comp)):
        if abs(comp[sym] - want[sym]) > TOL_ELEMENT:
            problems.append(f"{sym}: {comp[sym]!r} vs derived {want[sym]!r}")
    expect_density = None if lib_id in NULL_DENSITY else src.get("density_g_cm3")
    if entry.get("density_g_cm3") != expect_density:
        problems.append(f"density {entry.get('density_g_cm3')!r} vs {expect_density!r}")
    return problems


def expected_basis(lib_id):
    if lib_id in ("eurofer97-3-measured", "v-4cr-4ti"):
        return "measured_heat"
    if lib_id in ("eurofer97", "tungsten", "tantalum", "niobium", "titanium-grade2", "beryllium-s65"):
        return "specification_maxima"
    return "nominal"


def gate_g2(library=LIBRARY, sources=SOURCES):
    lib = json.loads(Path(library).read_text(encoding="utf-8"))
    record = json.loads(Path(sources).read_text(encoding="utf-8"))
    src_by_id = {m["id"]: m for m in record["materials"]}
    out = {"schema_ok": lib.get("schema") == SCHEMA and set(lib) == {"schema", "materials"}, "entries": {}}
    ids = [m.get("id") for m in lib.get("materials", [])]
    out["ids_ok"] = ids == IDS
    ok = out["schema_ok"] and out["ids_ok"]
    for entry in lib.get("materials", []):
        problems = check_entry(entry, src_by_id)
        if entry.get("basis") != expected_basis(entry.get("id")):
            problems.append(f"basis {entry.get('basis')!r}, protocol says {expected_basis(entry.get('id'))!r}")
        out["entries"][entry.get("id")] = problems or "ok"
        ok = ok and not problems
    out["pass"] = ok
    return out, lib


# ---------------------------------------------------------------- G3: the spectra

def boundaries_ascending(path=GROUPS):
    b = json.loads(Path(path).read_text(encoding="utf-8"))["boundaries_eV"]
    return b[::-1] if b[0] > b[-1] else list(b)


def dexp(x):
    return Decimal(x).exp() if x > -2000 else Decimal(0)


def maxwell_raw(bounds):
    """Group integrals of E exp(-E/kT): F(x) = 1 - (1 + x) e^-x in 100-digit arithmetic."""
    def cdf(e):
        x = Decimal(e) / Decimal(KT)
        return Decimal(1) - (1 + x) * dexp(-x) if x < 1500 else Decimal(1)
    c = [cdf(e) for e in bounds]
    return [c[i + 1] - c[i] for i in range(len(bounds) - 1)]


def erf_dec(z):
    """erf(z) for a float or Decimal z by its Taylor series in 100-digit arithmetic (|z| <= 8), else from math.erfc."""
    if abs(z) > 8:
        return Decimal(1 if z > 0 else -1) * (Decimal(1) - Decimal(math.erfc(float(abs(z)))))
    x = Decimal(z)
    term, total, n = x, x, 0
    while True:
        n += 1
        term = term * (-x * x) / n
        add = term / (2 * n + 1)
        total += add
        if abs(add) < Decimal(10) ** -95:
            break
    return total * 2 / pi_dec().sqrt()


_PI = []


def pi_dec():
    if not _PI:
        # Gauss-Legendre (AGM) iteration, 100 digits
        a, b, t, p = Decimal(1), Decimal(1) / Decimal(2).sqrt(), Decimal(1) / 4, Decimal(1)
        for _ in range(9):
            an = (a + b) / 2
            b = (a * b).sqrt()
            t -= p * (a - an) ** 2
            a, p = an, p * 2
        _PI.append((a + b) ** 2 / (4 * t))
    return _PI[0]


def erf_diff(z1, z2):
    if z1 > 8:
        return Decimal(math.erfc(float(z1))) - Decimal(math.erfc(float(z2)))
    return erf_dec(z2) - erf_dec(z1)


def watt_raw(bounds):
    """Group integrals of exp(-E/a) sinh(sqrt(bE)) from the completed-square closed form (error functions)."""
    # every quantity in 100-digit arithmetic: at low energy the group integral is a difference of
    # terms some 1e16 times larger, so even the error-function arguments cannot be rounded to float
    a, b = Decimal(WATT_A), Decimal(WATT_B)
    c = a * b.sqrt() / 2
    s = a.sqrt()
    pref = dexp(a * b / 4)
    sqrt_pi_a = (pi_dec() * a).sqrt()

    def gauss(z):
        return dexp(-z * z)

    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        u1, u2 = Decimal(lo).sqrt(), Decimal(hi).sqrt()
        zp1, zp2 = (u1 - c) / s, (u2 - c) / s
        zm1, zm2 = (u1 + c) / s, (u2 + c) / s
        g = gauss(zp2) - gauss(zp1) - gauss(zm2) + gauss(zm1)
        e = erf_diff(zp1, zp2) + erf_diff(zm1, zm2)
        out.append(pref * (-a / 2 * g + c * sqrt_pi_a / 2 * e))
    return out


def one_over_e_raw(bounds):
    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        lo2, hi2 = max(lo, ONE_OVER_E[0]), min(hi, ONE_OVER_E[1])
        out.append(math.log1p((hi2 - lo2) / lo2) if hi2 > lo2 else 0.0)
    return out


def expected_spectrum(shape, total, bounds):
    """Group values in ascending-energy order summing to `total`."""
    n = len(bounds) - 1
    if shape == "fusion-14mev":
        raw = [0.0] * n
        raw[next(i for i in range(n) if bounds[i] <= FUSION_EV < bounds[i + 1])] = 1.0
    elif shape == "fission":
        raw = watt_raw(bounds)
    elif shape == "thermal":
        raw = maxwell_raw(bounds)
    elif shape == "one-over-e":
        raw = one_over_e_raw(bounds)
    else:
        raise ValueError(shape)
    raw = [Decimal(v) for v in raw]
    s = sum(raw)
    return [float(v / s * Decimal(total)) for v in raw]


def phi(shape, e):
    if shape == "fission":
        return math.exp(-e / WATT_A) * math.sinh(math.sqrt(WATT_B * e))
    if shape == "thermal":
        return e * math.exp(-e / KT)
    if shape == "one-over-e":
        return 1.0 / e
    raise ValueError(shape)


def adaptive_simpson(f, a, b, tol=1e-13, depth=40):
    def simpson(fa, fm, fb, a, b):
        return (b - a) / 6.0 * (fa + 4.0 * fm + fb)

    def rec(a, b, fa, fm, fb, whole, tol, depth):
        m = 0.5 * (a + b)
        lm, rm = 0.5 * (a + m), 0.5 * (m + b)
        flm, frm = f(lm), f(rm)
        left, right = simpson(fa, flm, fm, a, m), simpson(fm, frm, fb, m, b)
        delta = left + right - whole
        if depth <= 0 or abs(delta) <= 15.0 * tol * max(abs(left + right), 1e-300):
            return left + right + delta / 15.0
        return (rec(a, m, fa, flm, fm, left, tol / 2, depth - 1)
                + rec(m, b, fm, frm, fb, right, tol / 2, depth - 1))

    fa, fb, fm = f(a), f(b), f(0.5 * (a + b))
    return rec(a, b, fa, fm, fb, simpson(fa, fm, fb, a, b), tol, depth)


def compare_spectrum(shape, written, total, bounds):
    """G3 for one shape: `written` is the ascending group list from the binary's output."""
    exp = expected_spectrum(shape, total, bounds)
    top = max(exp)
    worst, checked = 0.0, 0
    for w, e in zip(written, exp):
        if e > 1e-30 * top:
            checked += 1
            worst = max(worst, abs(w - e) / e)
    total_err = abs(sum(written) / total - 1.0)
    res = {"groups_checked": checked, "max_rel_error": worst, "total_rel_error": total_err}
    ok = worst <= 1e-10 and total_err <= 1e-12 and len(written) == len(exp)
    if shape == "fusion-14mev":
        hot = [i for i, w in enumerate(written) if w != 0.0]
        res["nonzero_groups"] = hot
        ok = ok and len(hot) == 1 and bounds[hot[0]] <= FUSION_EV < bounds[hot[0] + 1]
    else:
        order = sorted(range(len(exp)), key=lambda i: -exp[i])
        acc, chosen = 0.0, []
        for i in order:
            chosen.append(i)
            acc += exp[i]
            if acc >= 0.999 * total:
                break
        ref = chosen[0]
        q_ref = adaptive_simpson(lambda e: phi(shape, e), bounds[ref], bounds[ref + 1])
        worst_q = 0.0
        for i in chosen[1:]:
            q = adaptive_simpson(lambda e: phi(shape, e), bounds[i], bounds[i + 1])
            worst_q = max(worst_q, abs((q / q_ref) / (written[i] / written[ref]) - 1.0))
        res["quadrature_groups"] = len(chosen)
        res["quadrature_max_rel_error"] = worst_q
        ok = ok and worst_q <= 1e-8
    res["pass"] = ok
    return res


def ascending_from_document(doc):
    spectrum = doc["spectrum"]
    values = list(spectrum["flux_per_group"])
    if spectrum.get("descending"):
        values.reverse()
    scale = 1.0
    if spectrum.get("total") is not None:
        s = sum(values)
        scale = spectrum["total"] / s if s > 0 else 1.0
    return [v * scale for v in values], spectrum.get("structure")


# ---------------------------------------------------------------- running the binary

def run_bounded(cmd, cwd=None, env=None, timeout=TIMEOUT):
    """Run with a bounded wait; on timeout the process group is killed and reaped. (returncode, stderr tail)."""
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, start_new_session=True)
    try:
        _, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except OSError:
            p.kill()
        p.communicate()
        return None, f"timed out after {timeout} s"
    return p.returncode, err.decode(errors="replace")[-400:]


def gate_g3(binary, work, bounds):
    out = {}
    ok = True
    for shape in SHAPES:
        path = Path(work) / f"g3_{shape}.json"
        if path.exists():
            path.unlink()
        code, err = run_bounded([binary, "new", path, "--material", "ss316", "--source", shape, "--flux", "1e12"],
                                cwd=work, timeout=120)
        if code != 0 or not path.is_file():
            out[shape] = {"pass": False, "exit": code, "stderr": err}
            ok = False
            continue
        values, structure = ascending_from_document(json.loads(path.read_text()))
        res = compare_spectrum(shape, values, 1e12, bounds) if structure == "fispact-709" and len(values) == 709 \
            else {"pass": False, "problem": f"structure {structure!r} with {len(values)} groups"}
        out[shape] = res
        ok = ok and res["pass"]
    out["pass"] = ok
    return out


def gate_g4(lib):
    entry = next(m for m in lib["materials"] if m["id"] == "pb-15.7li")
    weights = entry.get("atomic_weights_used", {})
    li_w, pb_w = weights.get("Li"), weights.get("Pb")
    res = {"atomic_weights_used": weights}
    if li_w is None or pb_w is None or abs(li_w - 6.94) > 0.002 or abs(pb_w - 207.2) > 0.05:
        res.update({"pass": False, "problem": "atomic weights are not the standard values of natural Li and Pb"})
        return res
    want = pbli_weights(li_w, pb_w)
    comp = entry["composition_wt_percent"]
    errors = {k: abs(comp[k] / want[k] - 1.0) for k in want}
    res.update({"derived": want, "library": comp, "relative_errors": errors,
                "pass": all(e <= 1e-6 for e in errors.values())})
    return res


def problem_matches(problem, want):
    """Amendment A: the written problem carries the library composition (symbols without regard to case, 1e-9)."""
    material = problem.get("material") or {}
    comp = {k.capitalize(): v for k, v in (material.get("composition") or {}).items()}
    worst = max((abs(comp[k] - want[k]) for k in want if k in comp), default=None)
    ok = material.get("basis") == "wt_percent" and set(comp) == set(want) and worst is not None and worst <= 1e-9
    return ok, {"basis": material.get("basis"), "elements": sorted(comp), "max_abs_difference": worst}


def ledger_reads_back(ledger):
    """Amendment A: the result's ledger records a wt_percent composition totalling 100 with no unknown element."""
    seen = {k: ledger.get(k) for k in ("composition_basis", "composition_input_total",
                                       "composition_not_summing_to_100", "composition_elements_unknown")}
    total = seen["composition_input_total"]
    ok = (seen["composition_basis"] == "wt_percent" and isinstance(total, (int, float))
          and abs(total - 100.0) <= 1e-9 and seen["composition_not_summing_to_100"] is False
          and seen["composition_elements_unknown"] == [])
    return ok, seen


def gate_g5(binary, work, lib, data_dir):
    by_id = {m["id"]: m for m in lib["materials"]}
    out = {}
    ok = True
    env = dict(os.environ)
    if data_dir:
        env["ACTINV_DATA_DIR"] = str(data_dir)
    for material, shape, flux in CASES:
        name = f"{material}_{shape}"
        problem, result = Path(work) / f"g5_{name}.json", Path(work) / f"g5_{name}_result.json"
        for p in (problem, result):
            if p.exists():
                p.unlink()
        case = {}
        code, err = run_bounded([binary, "new", problem, "--material", material, "--source", shape, "--flux",
                                 f"{flux:g}"], cwd=work, env=env, timeout=120)
        case["new_exit"] = code
        if code == 0:
            before = sha256_file(problem)
            code, err = run_bounded([binary, "run", problem, result], cwd=work, env=env)
            case["problem_sha256"] = before
            case["problem_unchanged_by_run"] = sha256_file(problem) == before
            case["run_exit"] = code
            case["run_stderr"] = err if code != 0 else ""
        else:
            case["new_stderr"] = err
        if case["new_exit"] == 0:
            # the problem is read before the run, which uses the same file unchanged
            matches, case["problem_material"] = problem_matches(json.loads(problem.read_text()),
                                                                by_id[material]["composition_wt_percent"])
            case["problem_matches_library"] = matches
        good = (case.get("run_exit") == 0 and result.is_file() and case.get("problem_matches_library", False)
                and case.get("problem_unchanged_by_run", False))
        if case.get("run_exit") == 0 and result.is_file():
            back, case["result_ledger"] = ledger_reads_back(json.loads(result.read_text()).get("ledger") or {})
            case["ledger_reads_back"] = back
            good = good and back
        case["pass"] = good
        out[name] = case
        ok = ok and good
    out["pass"] = ok
    return out


def default_data_dir(cwd):
    env = os.environ.get("ACTINV_DATA_DIR")
    for candidate in ([Path(env)] if env else []) + [Path(cwd) / "actinv-data", ROOT / "actinv-data"]:
        if candidate.is_dir():
            return candidate.resolve()
    return None


# ---------------------------------------------------------------- selftest (no binary)

def selftest():
    def expect(cond, what):
        if not cond:
            raise SystemExit(f"selftest failed: {what}")
    bounds = boundaries_ascending()
    expect(len(bounds) == 710 and bounds == sorted(bounds), "709 ascending groups")
    # Decimal erf against math.erf
    for z in (-2.5, -0.3, 0.0, 0.7, 3.1, 7.9):
        expect(abs(float(erf_dec(z)) - math.erf(z)) < 1e-15, f"erf_dec({z})")
    expect(abs(float(pi_dec()) - math.pi) < 1e-15, "pi")
    expect(abs(float(erf_diff(9.0, 10.0)) / (math.erfc(9.0) - math.erfc(10.0)) - 1.0) < 1e-12, "erf tail difference")
    # spectra sum to the total, the fusion line sits in its group, Maxwellian and Watt integrate their closed form
    for shape in SHAPES:
        values = expected_spectrum(shape, 3e12, bounds)
        expect(len(values) == 709 and abs(sum(values) / 3e12 - 1.0) < 1e-12, f"{shape} total")
        expect(all(v >= 0.0 for v in values), f"{shape} nonnegative")
    fusion = expected_spectrum("fusion-14mev", 1.0, bounds)
    hot = [i for i, v in enumerate(fusion) if v]
    expect(len(hot) == 1 and bounds[hot[0]] <= FUSION_EV < bounds[hot[0] + 1], "fusion group")
    inv = expected_spectrum("one-over-e", 1.0, bounds)
    expect(inv[0] == 0.0 and inv[-1] == 0.0 and max(inv) > 0.0, "1/E vanishes outside 0.5 eV to 1 MeV")
    # a closed-form group against adaptive quadrature, and a corrupted value caught
    for shape in ("fission", "thermal"):
        values = expected_spectrum(shape, 1.0, bounds)
        res = compare_spectrum(shape, values, 1.0, bounds)
        expect(res["pass"] and res["quadrature_max_rel_error"] < 1e-8, f"{shape} self-comparison: {res}")
        bad = list(values)
        bad[max(range(709), key=lambda i: values[i])] *= 1.0 + 1e-6
        expect(not compare_spectrum(shape, bad, 1.0, bounds)["pass"], f"{shape} perturbation caught")
    expect(abs(adaptive_simpson(math.sin, 0.0, math.pi) - 2.0) < 1e-12, "adaptive Simpson")
    # Pb-Li conversion
    w = pbli_weights(6.94, 207.2)
    expect(abs(w["Li"] - 0.6199277898) < 1e-9 and abs(sum(w.values()) - 100.0) < 1e-12, "Pb-Li weights")
    # library entry rules on a small record
    record = {"w": {"id": "w_pure", "composition_wt_percent": {"W": 100.0}, "density_g_cm3": 19.3,
                    "impurity_limits_wt_percent": {"C": 0.01, "O": "0.010 (info)", "N": 0.01, "Fe": 0.01,
                                                   "Ni": 0.01, "Si": 0.01}}}
    want = expected_composition("tungsten", record["w"])
    expect(set(want) == {"C", "N", "Fe", "Ni", "Si", "W"} and abs(want["W"] - 99.95) < 1e-12, "tungsten rule")
    nominal = {"composition_wt_percent": {"Fe": 70.173, "Cr": 19.0, "Ni": 9.25, "Mn": 1.0, "C": 0.04, "Si": 0.5,
                                          "P": 0.023, "S": 0.015}}
    expect(abs(expected_composition("ss304", nominal)["Fe"] - 70.172) < 1e-12, "nominal sum taken up by the largest")
    expect(expected_composition("eurofer97-3-measured",
                                {"typical_analysis_wt_percent": {"Cr": 9.0, "Nb": "<0.0004"}})["Nb"] == 0.0004, "<x at x")
    entry = {"id": "tungsten", "name": "W", "category": "c", "basis": "specification_maxima", "note": "n",
             "density_g_cm3": 19.3, "source": {"citation": "c", "table": "t", "url": "u"},
             "composition_wt_percent": dict(want)}
    expect(check_entry(entry, {"w_pure": record["w"]}) == [], "a conforming entry passes")
    entry["composition_wt_percent"]["W"] += 1e-6
    expect(check_entry(entry, {"w_pure": record["w"]}), "a changed value is caught")
    entry["composition_wt_percent"]["W"] = want["W"]
    entry["composition_wt_percent"]["Xx"] = 1e-3
    expect(check_entry(entry, {"w_pure": record["w"]}), "an unknown symbol is caught")
    # Amendment A: the written problem and the result's ledger
    want = {"Fe": 99.0, "Cr": 1.0}
    expect(problem_matches({"material": {"basis": "wt_percent", "composition": {"FE": 99.0, "cr": 1.0}}}, want)[0],
           "a matching problem passes")
    expect(not problem_matches({"material": {"basis": "wt_percent", "composition": {"Fe": 99.0}}}, want)[0],
           "a missing element is caught")
    expect(not problem_matches({"material": {"basis": "atom_percent", "composition": dict(want)}}, want)[0],
           "the basis is checked")
    expect(not problem_matches({"material": {"basis": "wt_percent", "composition": {"Fe": 99.0, "Cr": 1.00001}}},
                               want)[0], "a changed value is caught")
    good = {"composition_basis": "wt_percent", "composition_input_total": 100.0,
            "composition_not_summing_to_100": False, "composition_elements_unknown": []}
    expect(ledger_reads_back(good)[0], "a ledger reading back 100 wt% passes")
    for key, bad in (("composition_input_total", 99.9), ("composition_not_summing_to_100", True),
                     ("composition_elements_unknown", ["Xx"]), ("composition_basis", "atom_percent")):
        expect(not ledger_reads_back(good | {key: bad})[0], f"ledger {key} caught")
    expect(not ledger_reads_back({})[0], "an empty ledger fails")
    # bounded wait kills a runaway child
    code, err = run_bounded([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    expect(code is None and "timed out" in err, "timeout is reported")
    # the real library and record
    g2, lib = gate_g2()
    expect(g2["pass"], f"the shipped library conforms: {g2}")
    expect(gate_g4(lib)["pass"], "Pb-Li entry")
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "g1.log"
        log.write_text("test x ... ok\nG1: PASS\n")
        expect(gate_g1(log)["pass"], "G1 log passes")
        log.write_text("test x ... FAILED\nG1: PASS\n")
        expect(not gate_g1(log)["pass"], "a failure fails G1")
    print("P162 selftest: PASS")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--actinv", default=os.environ.get("ACTINV_BIN", str(ROOT / "target" / "release" / "actinv")))
    ap.add_argument("--g1-log")
    ap.add_argument("--cwd", default=str(ROOT), help="directory holding actinv-data when ACTINV_DATA_DIR is unset")
    ap.add_argument("--work", default=str(ROOT / "target" / "p162-work"))
    args = ap.parse_args(argv)
    if args.selftest:
        selftest()
        return 0
    binary = Path(args.actinv).resolve()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    g0, g1 = gate_g0(), gate_g1(args.g1_log)
    g2, lib = gate_g2()
    g3 = gate_g3(binary, work, boundaries_ascending())
    g4 = gate_g4(lib)
    data_dir = default_data_dir(args.cwd)
    g5 = gate_g5(binary, work, lib, data_dir) if data_dir else {"pass": False, "problem": "no actinv-data directory"}
    gates = {"G0": g0["pass"], "G1": g1["pass"], "G2": g2["pass"], "G3": g3["pass"], "G4": g4["pass"],
             "G5": g5["pass"]}
    record = {
        "schema": "actinv-p162-library-1", "protocol": "protocols/ACTINV-P162_PROTOCOL.md",
        "protocol_sha256": sha256_file(PROTOCOL), "checker_sha256": sha256_file(Path(__file__)),
        "library_sha256": sha256_file(LIBRARY), "binary_sha256": sha256_file(binary) if binary.is_file() else None,
        "g0": g0, "g1": g1, "g2": g2, "g3": g3, "g4": g4, "g5": g5,
        "gates": {k: "PASS" if v else "FAIL" for k, v in gates.items()} | {"G6": "external"},
        "pass": all(gates.values()),
    }
    RECORD.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for k, v in gates.items():
        print(f"P162 {k}: {'PASS' if v else 'FAIL'}")
    print(f"P162: {'PASS' if record['pass'] else 'FAIL'} ({RECORD})")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
