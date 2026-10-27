#!/usr/bin/env python3
"""P153 checker (protocols/ACTINV-P153_PROTOCOL.md): fission-yield selection. Gates G2-G6 are evaluated here;
G0 is recorded for information, G1 (fmt, clippy, tests) and G7 (CI, committed baselines) are external.

Independence: everything below is rebuilt from the protocol, the activation library (streamed from the NPZ), the
ENDF/B-VIII.0 yield files (this script's own MF=8 MT=454/459 parser), the ENDF/B-VIII.0 + JEFF-3.3 decay files (the
P125 checker's parsers) and the user documentation. No engine source was consulted.

Common design of the physics runs: material 1e21 atoms/g U-235 + 1e21 atoms/g U-238, coupled mode, one 10 s
irradiation at flux multiplier 1 in a spectrum written on the library's 709-group structure and normalised so that the
U-235 fission rate per atom is R = 1e-11 1/s (burn-up 1e-9 of the sample). That makes the first-order solution of the
fission-product network exact to about 1e-8: the parents' depletion (R t = 1e-10), the capture loss of the products
(largest Xe-135: sigma phi t ~ 2e-7 in the thermal spectrum) and product-from-product reactions are all far below the
1e-6 tolerance used for the inventory comparisons.

  G2  Defaults unchanged. examples/fns_fe_5min.json, examples/shielding_demo.json and a U-235 case with the existing
      `"spectrum_average"` setting: reference and candidate results identical apart from timing keys (`ms`, and
      `wall_time_s`, `cells_per_s` wherever they occur).
  G3  Rate-weighted mixing. Spectra: thermal Maxwellian (kT = 0.0253 eV integrated over the library groups), FNS 14 MeV,
      and their sum scaled to equal U-235 fission rates. (a) w_k recomputed from the library MT=18 rows and the
      spectrum, compared with `ledger.fission_yield_weights` (and equal to the certificate's copy) at 1e-12 relative;
      `requested_energy_eV` of `fission_yield_selection` equals sum w_k E_k. (b)+(c) the mixed yields Y = sum w_k Y_k from
      this script's MT=454 read drive an independent first-order solution (matrix exponential of the decay network of
      the products, built from the decay files); every product above 1e-6 of the largest equals the run's inventory
      within 1e-6 relative. (d) A one-group spectrum reproduces `"fixed"` at the group's midpoint (steps equal at 1e-12;
      byte identity is reported). Information: relative differences from `"spectrum_average"` of the five largest
      fission-product activities in the mixed spectrum.
  G4  Selection. U-238 excluded: `fission_unselected_to_leakage` holds U-238 only and equals sum phi_g sigma_f,g (this
      script's own fold of the library row) within 1e-12; U-235 products equal a run whose only yield file is U-235
      (inventories at 1e-12); `include: [U235]` is identical to `exclude: [U238]` apart from request echoes; seven
      refusals each exit nonzero, write no output and name their cause.
  G5  Cumulative. First-order inventory from this script's MT=459 read (same network, 1e-6); `fission_cumulative_overlap`
      recomputed from the MT=459 yields and the decay edges (1e-12 on the yields, exact counts); certificate
      `fission_yields.yields == "cumulative"`.
  G6  Pathway filter. With `pathways.fission_parents: ["U235"]` no route has a fission edge out of U-238; the steps are
      identical to the unfiltered run; every route removed relative to the unfiltered run has a U-238 fission edge, the
      surviving routes are unchanged, and the filtered residual equals total minus the surviving routes.

Usage:
  check_p153.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p153-work]
      [--out results/p153_fission_yields.json] [--timeout 3600] [--reuse]
      [--data-dir actinv-data/v1.1.0] [--nfpy-dir ~/nuclear-data/endfb-viii.0-nfpy]
  check_p153.py --self-check        offline unit tests (no binary, no data)

Run inside the required systemd cgroup. Needs numpy and scipy. Exit 0 on PASS, 1 on FAIL.
"""
import argparse
import bisect
import copy
import json
import math
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_p122 import (canonical, cont, name_of, read_list, sha256_file, strip_timing,  # noqa: E402
                        SYMBOLS)
from check_p125 import Library, ascending_flux, decay_edges, fold, merge_decay, resolve_product  # noqa: E402

ROOT = HERE.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P153_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEMO_SPEC = ROOT / "examples" / "shielding_demo.json"
LIBRARY_NAME = "tendl-2025-neutron-709g.npz"
DECAY_PRIMARY_NAME = "endf-b-viii-0_decay.dat"
DECAY_FALLBACK_NAME = "jeff-3-3_decay.dat"

# Pinned inputs (sha256sum of the files in ~/nuclear-data/endfb-viii.0-nfpy, computed 2026-10-27).
YIELD_SHA256 = {
    "nfy-092_U_235.endf": "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41",
    "nfy-092_U_238.endf": "05598b1c16117a729a3314842a2de574ffb7c992f679f6e1505c1d566164791c",
}
U235, U238, U234 = (92235, 0), (92238, 0), (92234, 0)
PARENTS = (U235, U238)
KT_EV = 0.0253
N0 = 1.0e21                 # atoms/g of each parent
RATE_U235 = 1.0e-11         # U-235 fission rate per atom and second of every physics spectrum
IRRADIATION_S = 10.0
ONE_GROUP_TARGET_EV = 2.0e6
CUMULATIVE_EV = 2.0e6

TOL_WEIGHT = 1e-12
WEIGHT_ABS_FLOOR = 1e-18    # weights below this are 1e-18 of the total and immaterial
TOL_FIRST_ORDER = 1e-6
TOL_EXACT = 1e-12
FLOOR_FRACTION = 1e-4       # compare products above this fraction of the largest expected population. The dense
                            # expm has an absolute error of about 3e-11 of the largest entry (observed: Cu72, Nb96 at
                            # 1e-5..1e-6 of the maximum, 1 atom/g of 3e10), so relative noise is ~3e-11/floor: 3e-7 here
MAX_LISTED = 20
TIMING_TOP = ("ms",)
TIMING_ANY = ("wall_time_s", "cells_per_s")
ECHO_TOKENS = ("parents", "sha256", "spec", "request", "title")


def rel_err(got, exp):
    if got is None or exp is None or not (isinstance(got, (int, float)) and isinstance(exp, (int, float))):
        return math.inf
    if not (math.isfinite(got) and math.isfinite(exp)):
        return math.inf
    if exp == 0.0:
        return 0.0 if got == 0.0 else math.inf
    return abs(got - exp) / abs(exp)


# ------------------------------------------------------------------------------ ENDF-6 MF=8 yield tables


def read_yield_file(path, mt):
    """MF=8 section `mt` (454 independent, 459 cumulative) of an ENDF-6 neutron fission-yield file.
    Returns None when the section is absent, else {"za", "energies", "interp", "tables"} with
    tables[k] = {(ZAFP, FPS): (yield, uncertainty)} for incident energy k. HEAD: ZA, AWR, LE, 0, 0, 0; then one LIST
    per energy: E, 0, interpolation, 0, 4*NFP, NFP followed by (ZAFP, FPS, Y, DY) quadruples."""
    tag = f" 8{mt}"
    lines = []
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) >= 75 and line[70:75] == tag:
                lines.append(line)
    if not lines:
        return None
    head = cont(lines[0])
    energies, interp, tables = [], [], []
    index = 1
    while index < len(lines):
        hd, values, index = read_list(lines, index)
        table = {}
        if hd[4] != 4 * hd[5] or len(values) != hd[4]:
            raise ValueError(f"{path}: MT={mt} LIST length {hd[4]} is not 4*NFP={4 * hd[5]}")
        for k in range(hd[5]):
            key = (int(round(values[4 * k])), int(round(values[4 * k + 1])))
            if key in table:
                raise ValueError(f"{path}: duplicate product {key} at E={hd[0]}")
            table[key] = (values[4 * k + 2], values[4 * k + 3])
        energies.append(hd[0])
        interp.append(hd[2])
        tables.append(table)
    if head[2] and head[2] != len(energies):
        raise ValueError(f"{path}: MT={mt} announces {head[2]} energies, found {len(energies)}")
    if any(b <= a for a, b in zip(energies, energies[1:])):
        raise ValueError(f"{path}: incident energies are not ascending")
    return {"za": int(round(head[0])), "energies": energies, "interp": interp, "tables": tables}


# ------------------------------------------------------------------------------ mixing arithmetic


def hats(energy, grid):
    """Piecewise-linear hat weights of `energy` on the ascending tabulated `grid`, clamped at both ends."""
    h = [0.0] * len(grid)
    if energy <= grid[0]:
        h[0] = 1.0
    elif energy >= grid[-1]:
        h[-1] = 1.0
    else:
        j = bisect.bisect_right(grid, energy) - 1
        w = (energy - grid[j]) / (grid[j + 1] - grid[j])
        h[j], h[j + 1] = 1.0 - w, w
    return h


def group_midpoints(bounds):
    return [math.sqrt(bounds[g] * bounds[g + 1]) for g in range(len(bounds) - 1)]


def rate_weights(sigma, flux, bounds, grid):
    """w_k = sum_g r_g h_k(E_g) / sum_g r_g with r_g = flux_g sigma_g; None when the parent has no fission rate."""
    rates = [f * s for f, s in zip(flux, sigma)]
    total = math.fsum(rates)
    if not total > 0.0:
        return None
    mids = group_midpoints(bounds)
    acc = [[] for _ in grid]
    for r, e in zip(rates, mids):
        if r == 0.0:
            continue
        for k, hk in enumerate(hats(e, grid)):
            if hk != 0.0:
                acc[k].append(r * hk)
    return [math.fsum(a) / total for a in acc]


def mixed_yield(table, weights):
    """Y = sum_k w_k Y_k over the union of products (a product missing from a table counts as zero)."""
    out = {}
    for k, wk in enumerate(weights):
        if wk == 0.0:
            continue
        for key, (y, _dy) in table["tables"][k].items():
            out[key] = out.get(key, 0.0) + wk * y
    return out


def maxwellian_groups(bounds, kt):
    """Group flux of the Maxwellian flux spectrum phi(E) ~ E exp(-E/kT)/kT^2 (unit total): the exact integral
    F(lo) - F(hi) with F(E) = (1 + E/kT) exp(-E/kT)."""
    def big_f(energy):
        a = energy / kt
        return (1.0 + a) * math.exp(-a)
    return [big_f(bounds[g]) - big_f(bounds[g + 1]) for g in range(len(bounds) - 1)]


# ------------------------------------------------------------------------------ yield products, network, overlap


def parse_parent_key(text):
    """(ZA, LISO) from '92235_0', '92_235' (Z_A) or 'U235' / 'Am242m1'."""
    if isinstance(text, (list, tuple)) and len(text) == 2:
        return int(text[0]), int(text[1])
    m = re.fullmatch(r"(\d+)_(\d+)", str(text))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (a, b) if a >= 1000 else (a * 1000 + b, 0)
    m = re.fullmatch(r"([A-Z][a-z]?)(\d+)(?:m(\d+))?", str(text))
    if m and m.group(1) in SYMBOLS:
        return (SYMBOLS.index(m.group(1)) + 1) * 1000 + int(m.group(2)), int(m.group(3) or 0)
    return None


def map_products(records, yields, mapping="exact"):
    """Yield-table products onto chain states. The engine (verified against its runs, 2026-10-27) uses the exact
    (ZAFP, FPS) state only: a product whose state is not in the chain, including an isomer FPS=1 entry whose isomer has
    no decay record, goes to leakage and does NOT fall back to the ground state (which would double count the many
    FPS=0/FPS=1 pairs of the evaluation). mapping="resolved" is kept only for the self-check."""
    out = {}
    for (za, fps), y in yields.items():
        if y <= 0.0:
            continue
        key = (za, fps) if (mapping == "exact" and (za, fps) in records) else \
            (resolve_product(records, za, fps) if mapping == "resolved" else None)
        if key is not None:
            out[key] = out.get(key, 0.0) + y
    return out


def overlap_diagnostic(records, yields, mapping="exact"):
    """Independent recomputation of `fission_cumulative_overlap` for one parent. A product overlaps when its yield is
    positive, it is a chain nuclide, and a chain nuclide with a positive off-diagonal decay edge into it also has a
    positive yield from the same parent."""
    mapped = map_products(records, yields, mapping)
    overlap = set()
    for q in mapped:
        for d, rate in decay_edges(records, q)["edges"].items():
            if rate > 0.0 and d != q and d in mapped:
                overlap.add(d)
    summed_all = math.fsum(yields.values())
    summed_mapped = math.fsum(mapped.values())
    return {"summed_all_tabulated": summed_all, "summed_mapped": summed_mapped,
            "overlap_yield": math.fsum(mapped[p] for p in overlap), "overlap_products": len(overlap),
            "products_with_yield_mapped": len(mapped),
            "products_with_yield_tabulated": sum(1 for y in yields.values() if y > 0.0)}


def first_order(records, sources, seconds):
    """Exact solution of dN/dt = A N + S, N(0) = 0 over `seconds` for each source vector S (atoms/g/s, keyed by chain
    state), A the decay network of the sources and their descendants (decay constants and branches as P125 rebuilds
    them from the decay files). One matrix exponential of the augmented matrix serves every source."""
    import numpy as np
    from scipy.linalg import expm
    states, index, stack = [], {}, sorted({k for s in sources for k in s})
    while stack:
        key = stack.pop()
        if key in index:
            continue
        index[key] = len(states)
        states.append(key)
        if key in records:
            stack.extend(d for d in decay_edges(records, key)["edges"] if d not in index)
    n, k = len(states), len(sources)
    m = np.zeros((n + k, n + k))
    for key, col in index.items():
        if key in records:
            info = decay_edges(records, key)
            m[col, col] -= info["lam"]
            for d, rate in info["edges"].items():
                m[index[d], col] += rate
    for j, source in enumerate(sources):
        for key, s in source.items():
            m[index[key], n + j] += s
    e = expm(m * seconds)
    return [{key: float(e[i, n + j]) for key, i in index.items()} for j in range(k)]


def compare_inventory(run, expected, tol):
    """Run inventory vs expected populations (atoms/g) above FLOOR_FRACTION of the largest expected one."""
    inv = {row["nuclide"]: row["atoms_per_g"] for row in run["steps"][-1]["inventory"]}
    top = max(expected.values())
    floor = FLOOR_FRACTION * top
    worst, bad, compared, absent = 0.0, [], 0, []
    for key, exp in sorted(expected.items()):
        if exp < floor:
            continue
        name = name_of(key[0] // 1000, key[0] % 1000, key[1])
        got = inv.get(name)
        if got is None:
            absent.append(name)
            continue
        compared += 1
        err = rel_err(got, exp)
        worst = max(worst, err)
        if err > tol:
            bad.append({"nuclide": name, "run": got, "expected": exp, "rel": err})
    return {"compared": compared, "worst_relative_error": worst, "tolerance": tol, "floor_atoms_per_g": floor,
            "largest_expected_atoms_per_g": top, "absent_from_run": absent[:MAX_LISTED], "n_absent": len(absent),
            "bad": sorted(bad, key=lambda b: -b["rel"])[:MAX_LISTED], "n_bad": len(bad),
            "pass": compared > 0 and not bad and not absent}


# ------------------------------------------------------------------------------ result-reading helpers


def find_parent_entries(obj, keyword, hint=None, out=None):
    """Every dict carrying `keyword`, with its parent (ZA, LISO) taken from its own fields or the key above it."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        if keyword in obj:
            parent = None
            if "parent_ZA" in obj:
                parent = (int(obj["parent_ZA"]), int(obj.get("parent_LISO", 0)))
            elif "parent" in obj:
                parent = parse_parent_key(obj["parent"])
            if parent is None and hint is not None:
                parent = parse_parent_key(hint)
            out.setdefault(parent, []).append(obj)
        else:
            for k, v in obj.items():
                find_parent_entries(v, keyword, k, out)
    elif isinstance(obj, list):
        for v in obj:
            find_parent_entries(v, keyword, hint, out)
    return out


def ledger_of(result):
    led = result.get("ledger")
    return led if isinstance(led, dict) else {}


def certificate_yields(result):
    cert = result.get("certificate")
    cert = cert.get("fission_yields") if isinstance(cert, dict) else None
    return cert if isinstance(cert, dict) else {}


def keyed_rates(obj):
    """{(ZA, LISO): number} from a ledger mapping keyed '92235_0' or '92_235'."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            parent = parse_parent_key(k)
            if parent is not None and isinstance(v, (int, float)):
                out[parent] = v
    return out


def numeric_diff(a, b, tol, path="", out=None, limit=60):
    """Structure must match; numeric leaves may differ by `tol` relative. Returns the list of differing paths."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in one result only")
            else:
                numeric_diff(a[k], b[k], tol, f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            numeric_diff(x, y, tol, f"{path}[{i}]", out, limit)
    elif isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str) or a is None or b is None:
        if a != b:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a != b and rel_err(a, b) > tol:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


def all_differences(a, b, path="", out=None, limit=400):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in one result only")
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


def is_echo(difference):
    path = difference.split(":")[0].lower()
    return any(token in path for token in ECHO_TOKENS)


# ------------------------------------------------------------------------------ process handling


def run_binary(binary, spec_path, out_path, log_path, cwd, timeout):
    """`binary run SPEC OUT` in its own session; on timeout the whole group is killed and reaped."""
    if Path(out_path).exists():
        Path(out_path).unlink()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen([str(binary), "run", str(spec_path), str(out_path)], cwd=str(cwd), stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return proc.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait()
            raise


class Harness:
    def __init__(self, work, timeout, reuse, cwd):
        self.work, self.timeout, self.reuse, self.cwd = work, timeout, reuse, cwd
        self.runs = {}

    def execute(self, label, binary, spec):
        spec_path, out_path, log_path = (self.work / f"{label}.spec.json", self.work / f"{label}.json",
                                         self.work / f"{label}.log")
        if isinstance(spec, dict):
            spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        else:
            spec_path = Path(spec)
        info = {"binary": str(binary), "spec": str(spec_path), "spec_sha256": sha256_file(spec_path)}
        self.runs[label] = info
        if not (self.reuse and out_path.exists()):
            try:
                code = run_binary(binary, spec_path, out_path, log_path, self.cwd, self.timeout)
            except subprocess.TimeoutExpired:
                info["error"] = f"timed out after {self.timeout} s"
                raise RuntimeError(info["error"])
            info["exit_code"] = code
            if code != 0:
                info["error"] = f"exit code {code}; see {log_path}"
                raise RuntimeError(info["error"] + ": " + tail(log_path))
        info["output_sha256"] = sha256_file(out_path)
        return json.loads(out_path.read_text())

    def refuse(self, label, binary, spec):
        """Run a spec that must be refused. Returns (exit code, message, output file written)."""
        spec_path, out_path, log_path = (self.work / f"{label}.spec.json", self.work / f"{label}.json",
                                         self.work / f"{label}.log")
        spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        try:
            code = run_binary(binary, spec_path, out_path, log_path, self.cwd, min(self.timeout, 600))
        except subprocess.TimeoutExpired:
            return None, "timed out", out_path.exists()
        return code, tail(log_path, 2000), out_path.exists()


def tail(path, size=600):
    try:
        return Path(path).read_text(errors="replace")[-size:].strip()
    except OSError:
        return ""


# ------------------------------------------------------------------------------ spec construction


class Inputs:
    """Everything the physics runs and their independent recomputation share."""

    def __init__(self, data_dir, nfpy_dir):
        self.library_path = data_dir / "activation" / LIBRARY_NAME
        self.decay_primary = data_dir / "decay" / DECAY_PRIMARY_NAME
        self.decay_fallback = data_dir / "decay" / DECAY_FALLBACK_NAME
        self.nfpy_dir = nfpy_dir
        self.yield_files = {}
        for name, digest in YIELD_SHA256.items():
            path = nfpy_dir / name
            actual = sha256_file(path)
            if actual != digest:
                raise RuntimeError(f"{path}: sha256 {actual} differs from the pinned {digest}")
            self.yield_files[name] = {"path": str(path), "sha256": actual}
        fns = json.loads(FNS_SPEC.read_text())
        self.library_sha256 = sha256_file(self.library_path)
        if self.library_sha256 != fns["library"]["sha256"]:
            raise RuntimeError("the activation library differs from the hash pinned in examples/fns_fe_5min.json")
        self.fns_flux = ascending_flux(fns["spectrum"])
        self.lib = Library(self.library_path)
        self.bounds = self.lib.bounds
        self.records, _nubar, self.decay_info = merge_decay(self.decay_primary, self.decay_fallback)
        rows = {}
        for key in PARENTS + (U234,):
            rows[key] = [i for i in self.lib.rows_of(key) if self.lib.rows[i][1] == 18 and self.lib.rows[i][2] == 0]
        streamed = self.lib.stream([i for v in rows.values() for i in v])
        self.sigma = {}
        for key, idx in rows.items():
            if idx:
                self.sigma[key] = [math.fsum(streamed[i][g] for i in idx) for g in range(self.lib.n_groups)]
        self.tables = {}
        self.cumulative = {}
        for key, name in ((U235, "nfy-092_U_235.endf"), (U238, "nfy-092_U_238.endf")):
            path = self.yield_files[name]["path"]
            self.tables[key] = read_yield_file(path, 454)
            self.cumulative[key] = read_yield_file(path, 459)
            if self.tables[key] is None or self.tables[key]["za"] != key[0]:
                raise RuntimeError(f"{path}: no MT=454 section for the expected parent")
        self.fe56_yield_file = None
        # spectra on the library's ascending groups
        n = self.lib.n_groups
        thermal = maxwellian_groups(self.bounds, KT_EV)
        self.spectra = {"thermal": self.normalise(thermal), "fns": self.normalise(self.fns_flux)}
        self.spectra["mix"] = [a + b for a, b in zip(self.spectra["thermal"], self.spectra["fns"])]
        mids = group_midpoints(self.bounds)
        g0 = min(range(n), key=lambda g: abs(math.log(mids[g] / ONE_GROUP_TARGET_EV)))
        single = [0.0] * n
        single[g0] = 1.0
        self.one_group_index, self.one_group_midpoint = g0, mids[g0]
        self.spectra["one_group"] = self.normalise(single)

    def make_fe56_yield_file(self, work):
        """Copy of the U-235 file with ZA 92235 -> 26056 in every HEAD line (Fe-56 has no MT=18 row in the library)."""
        target = work / "nfy-026_Fe_056.synthetic_from_U235.endf"
        with open(self.yield_files["nfy-092_U_235.endf"]["path"], "r", encoding="ascii", errors="replace") as src, \
                open(target, "w", encoding="ascii") as dst:
            for line in src:
                if line.startswith(" 9.223500+4"):
                    line = " 2.605600+4" + line[11:]
                dst.write(line)
        self.fe56_yield_file = {"path": str(target), "sha256": sha256_file(target)}
        if 92235 in [k for t in (read_yield_file(target, 454),) for k in (t["za"],)]:
            raise RuntimeError("Fe-56 yield file generation left ZA 92235 in place")
        return self.fe56_yield_file

    def normalise(self, flux):
        scale = RATE_U235 / fold(self.sigma[U235], flux)
        return [f * scale for f in flux]

    def rate(self, parent, flux):
        return fold(self.sigma[parent], flux)

    def spec(self, title, flux, yields, schedule=None, parents=None, files=("nfy-092_U_235.endf",
                                                                           "nfy-092_U_238.endf"),
             composition=None, extra_options=None, pathways=None):
        total = 0.0
        for v in flux:
            total += v
        spec = {"spec": "actinv-spec-1", "title": title, "projectile": "neutron",
                "library": {"path": str(self.library_path), "sha256": self.library_sha256},
                "decay": {"primary": str(self.decay_primary), "fallback": str(self.decay_fallback)},
                "material": {"mass_g": 1.0, "basis": "atoms_per_g",
                             "composition": composition or {"U235": N0, "U238": N0}},
                "spectrum": {"structure": "fispact-709", "flux_per_group": list(flux), "total": total},
                "fission_yields": {"files": [dict(self.yield_files[f]) for f in files]},
                "schedule": schedule or [{"dt": f"{IRRADIATION_S:g} s", "flux": 1.0}],
                "options": {"mode": "coupled", "prune": "rate", "bmin_atoms_per_g": 1e-8, "temperature_K": 293.6}}
        spec["fission_yields"].update(yields)
        if parents is not None:
            spec["fission_yields"]["parents"] = parents
        if extra_options:
            spec["options"].update(extra_options)
        if pathways is not None:
            spec["pathways"] = pathways
        return spec

    def source_terms(self, flux, per_parent_yields):
        """S_p = N0 * r_parent * Y_p (atoms/g/s) on the chain states, summed over parents."""
        source = {}
        for parent, yields in per_parent_yields.items():
            r = self.rate(parent, flux)
            for key, y in map_products(self.records, yields).items():
                source[key] = source.get(key, 0.0) + N0 * r * y
        return source


# ------------------------------------------------------------------------------ gates


def guarded(fn):
    try:
        return fn()
    except (RuntimeError, KeyError, ValueError, IndexError, TypeError, ImportError, ZeroDivisionError,
            AttributeError, OSError) as error:
        return {"pass": False, "error": f"{type(error).__name__}: {error}"}


def gate_g2(h, ref, cand, inputs, data_cwd):
    cases = {}
    u235_only = inputs.spec("P153 G2: U-235 with spectrum_average", inputs.spectra["fns"],
                            {"energy": "spectrum_average"}, files=("nfy-092_U_235.endf",),
                            composition={"U235": N0})
    for label, spec in (("fns_fe_5min", FNS_SPEC), ("shielding_demo", DEMO_SPEC), ("u235_spectrum_average", u235_only)):
        def one(label=label, spec=spec):
            a = h.execute(f"g2_{label}_reference", ref, spec)
            b = h.execute(f"g2_{label}_candidate", cand, spec)
            sa, dropped_a = strip_timing(a)
            sb, dropped_b = strip_timing(b)
            same = canonical(sa) == canonical(sb)
            out = {"identical_apart_from_timing": same, "timing_keys_dropped": sorted(set(dropped_a + dropped_b)),
                   "pass": same}
            if not same:
                out["first_differences"] = all_differences(sa, sb, limit=10)
            return out
        cases[label] = guarded(one)
    return {"pass": all(c.get("pass") for c in cases.values()), "cases": cases}


def check_weight_entries(result, inputs, flux, label):
    """(a): ledger and certificate weights, selection energies, against the independent recomputation."""
    ledger = ledger_of(result).get("fission_yield_weights")
    cert = certificate_yields(result).get("weights")
    out = {"parents": {}, "pass": False}
    if not isinstance(ledger, list) or not isinstance(cert, list):
        out["error"] = "ledger.fission_yield_weights or certificate.fission_yields.weights is not a list"
        return out
    out["certificate_equals_ledger"] = canonical(ledger) == canonical(cert)
    selection_ledger = find_parent_entries(ledger_of(result).get("fission_yield_selection"), "requested_energy_eV")
    selection_cert = find_parent_entries(certificate_yields(result), "requested_energy_eV")
    ok = out["certificate_equals_ledger"]
    for parent in PARENTS:
        table = inputs.tables[parent]
        expected = rate_weights(inputs.sigma[parent], flux, inputs.bounds, table["energies"])
        entries = [e for e in ledger if isinstance(e, dict) and e.get("parent_ZA") == parent[0]
                   and e.get("parent_LISO", 0) == parent[1] and e.get("spectrum", 0) == 0]
        info = {"entries": len(entries), "expected_weights": expected}
        if len(entries) != 1:
            info["error"] = "expected exactly one ledger entry for the base spectrum"
            ok = False
        else:
            entry = entries[0]
            got_e = entry.get("energies_eV")
            info["energies_match"] = (isinstance(got_e, list) and len(got_e) == len(table["energies"])
                                      and all(rel_err(x, y) <= 1e-14 for x, y in zip(got_e, table["energies"])))
            weights = entry.get("weights")
            if expected is None:
                info["fallback_recorded"] = weights is None and entry.get("fallback") == "spectrum_average"
                good = info["fallback_recorded"] and info["energies_match"]
            else:
                if not isinstance(weights, list) or len(weights) != len(expected):
                    info["error"] = "weights missing or wrong length"
                    good = False
                else:
                    worst = max(0.0 if abs(g - e) <= WEIGHT_ABS_FLOOR else rel_err(g, e)
                                for g, e in zip(weights, expected))
                    info["run_weights"] = weights
                    info["worst_relative_error"] = worst
                    info["sum_of_weights"] = math.fsum(weights)
                    good = (worst <= TOL_WEIGHT and info["energies_match"]
                            and abs(math.fsum(weights) - 1.0) <= 1e-12)
                    mean_energy = math.fsum(w * e for w, e in zip(expected, table["energies"]))
                    info["expected_requested_energy_eV"] = mean_energy
                    for where, found in (("ledger", selection_ledger), ("certificate", selection_cert)):
                        hits = found.get(parent, [])
                        values = [h["requested_energy_eV"] for h in hits]
                        info[f"selection_{where}_requested_energy_eV"] = values
                        if not values or any(rel_err(v, mean_energy) > TOL_WEIGHT for v in values):
                            good = False
                            info[f"selection_{where}_mismatch_or_missing"] = True
            ok = ok and bool(good)
        out["parents"][f"{parent[0]}_{parent[1]}"] = info
    out["pass"] = bool(ok)
    return out


def fission_product_names(inputs):
    names = set()
    for parent in PARENTS:
        for table in inputs.tables[parent]["tables"]:
            for za, fps in table:
                names.add(name_of(za // 1000, za % 1000, fps))
    return names


def gate_g3(h, cand, inputs):
    cases, fp_names = {}, fission_product_names(inputs)
    for label in ("thermal", "fns", "mix"):
        def one(label=label):
            flux = inputs.spectra[label]
            rw = h.execute(f"g3_{label}_rate_weighted", cand,
                           inputs.spec(f"P153 G3 {label}, rate_weighted", flux, {"energy": "rate_weighted"}))
            case = {"weights": check_weight_entries(rw, inputs, flux, label)}
            per_parent = {}
            for parent in PARENTS:
                w = rate_weights(inputs.sigma[parent], flux, inputs.bounds, inputs.tables[parent]["energies"])
                if w is not None:
                    per_parent[parent] = mixed_yield(inputs.tables[parent], w)
            expected = first_order(inputs.records, [inputs.source_terms(flux, per_parent)], IRRADIATION_S)[0]
            case["first_order_inventory"] = compare_inventory(rw, expected, TOL_FIRST_ORDER)
            case["fission_rates_per_atom_per_s"] = {f"{p[0]}": inputs.rate(p, flux) for p in PARENTS}
            case["pass"] = bool(case["weights"]["pass"] and case["first_order_inventory"]["pass"])
            if label == "mix":
                sa = h.execute("g3_mix_spectrum_average", cand,
                               inputs.spec("P153 G3 mix, spectrum_average", flux, {"energy": "spectrum_average"}))
                act_rw = rw["steps"][-1]["activity_Bq_per_g"]
                act_sa = sa["steps"][-1]["activity_Bq_per_g"]
                top = sorted(((v, k) for k, v in act_rw.items() if k in fp_names), reverse=True)[:5]
                case["information_top5_fission_product_activity_vs_spectrum_average"] = [
                    {"nuclide": k, "rate_weighted_Bq_per_g": v, "spectrum_average_Bq_per_g": act_sa.get(k),
                     "relative_difference": (None if not act_sa.get(k) else (v - act_sa[k]) / act_sa[k])}
                    for v, k in top]
            return case
        cases[label] = guarded(one)

    def one_group():
        flux = inputs.spectra["one_group"]
        rw = h.execute("g3_one_group_rate_weighted", cand,
                       inputs.spec("P153 G3 one group, rate_weighted", flux, {"energy": "rate_weighted"}))
        fx = h.execute("g3_one_group_fixed", cand,
                       inputs.spec("P153 G3 one group, fixed at the midpoint", flux,
                                   {"energy": "fixed", "fixed_energy_eV": inputs.one_group_midpoint}))
        differences = numeric_diff(rw["steps"], fx["steps"], TOL_EXACT)
        balance = numeric_diff(ledger_of(rw).get("fission_yield_balance"),
                               ledger_of(fx).get("fission_yield_balance"), TOL_EXACT)
        weights = check_weight_entries(rw, inputs, flux, "one_group")
        return {"group_index": inputs.one_group_index, "midpoint_eV": inputs.one_group_midpoint,
                "steps_byte_identical": canonical(rw["steps"]) == canonical(fx["steps"]),
                "step_differences_above_tolerance": differences[:MAX_LISTED], "balance_differences": balance[:5],
                "weights": weights, "tolerance": TOL_EXACT,
                "pass": bool(not differences and not balance and weights["pass"])}
    cases["one_group"] = guarded(one_group)
    return {"pass": all(c.get("pass") for c in cases.values()), "cases": cases,
            "scope_note": "mixed yields are verified through the first-order inventory (1e-6), not per product at "
                          "1e-12: the run does not print per-product effective yields"}


def refusal_cases(inputs):
    flux = inputs.spectra["mix"]
    base = inputs.spec("P153 G4 refusal base", flux, {"energy": "spectrum_average"})
    cases = {}

    def variant(key, mutate):
        spec = copy.deepcopy(base)
        mutate(spec["fission_yields"])
        cases[key] = spec

    variant("include_and_exclude", lambda f: f.update(parents={"include": ["U235"], "exclude": ["U238"]}))
    variant("neither", lambda f: f.update(parents={}))
    variant("empty_list", lambda f: f.update(parents={"include": []}))
    variant("no_yield_file", lambda f: f.update(parents={"include": ["U234"]}))
    # Th-232 has an MT=18 row in this library, so it cannot serve. Honest construction: a copy of the U-235 yield file
    # whose parent ZA is rewritten to Fe-56 (a library target with no fission row), generated and hash-pinned here.
    fe = inputs.fe56_yield_file
    thorium = inputs.spec("P153 G4 refusal non-fissionable parent", flux, {"energy": "spectrum_average"},
                          parents={"include": ["Fe56"]}, files=("nfy-092_U_235.endf",))
    thorium["fission_yields"]["files"].append(dict(fe))
    cases["not_fissionable"] = thorium
    variant("rate_weighted_with_fixed_energy", lambda f: f.update(energy="rate_weighted", fixed_energy_eV=0.0253))
    expect = {"include_and_exclude": [("include", "exclude")], "neither": [("include", "exclude", "parents")],
              "empty_list": [("empty", "include", "parents", "at least")],
              "no_yield_file": [("has no fission-yield file",)],
              "not_fissionable": [("not fissionable in the activation library",)],
              "rate_weighted_with_fixed_energy": [("fixed_energy_eV", "rate_weighted")],
              "cumulative_without_mt459": [("459", "cumulative")]}
    return cases, expect


def gate_g4(h, cand, inputs, work):
    flux = inputs.spectra["mix"]
    out = {"cases": {}}

    def selection():
        exclude = h.execute("g4_exclude_u238", cand, inputs.spec("P153 G4 selection", flux,
                                                                {"energy": "spectrum_average"},
                                                                parents={"exclude": ["U238"]}))
        include = h.execute("g4_include_u235", cand, inputs.spec("P153 G4 selection", flux,
                                                                {"energy": "spectrum_average"},
                                                                parents={"include": ["U235"]}))
        only = h.execute("g4_u235_file_only", cand, inputs.spec("P153 G4 selection", flux,
                                                               {"energy": "spectrum_average"},
                                                               files=("nfy-092_U_235.endf",)))
        info = {}
        unselected = keyed_rates(ledger_of(exclude).get("fission_unselected_to_leakage"))
        expected = inputs.rate(U238, flux)
        info["unselected_keys"] = sorted(f"{k[0]}_{k[1]}" for k in unselected)
        info["u238_fission_rate_independent"] = expected
        info["u238_fission_rate_ledger"] = unselected.get(U238)
        info["u238_relative_error"] = rel_err(unselected.get(U238), expected)
        no_yields = keyed_rates(ledger_of(exclude).get("fission_no_yields_to_leakage"))
        info["u238_in_no_yields_to_leakage"] = U238 in no_yields
        info["unselected_is_u238_only"] = set(unselected) == {U238}
        diffs = numeric_diff(exclude["steps"], only["steps"], TOL_EXACT)
        info["exclude_vs_u235_file_only_step_differences"] = diffs[:MAX_LISTED]
        info["exclude_vs_u235_file_only_byte_identical_steps"] = canonical(exclude["steps"]) == canonical(only["steps"])
        a, _ = strip_timing(exclude)
        b, _ = strip_timing(include)
        diff = all_differences(a, b)
        real = [d for d in diff if not is_echo(d)]
        info["include_vs_exclude_differences"] = diff[:MAX_LISTED]
        info["include_vs_exclude_non_echo_differences"] = real[:MAX_LISTED]
        info["include_vs_exclude_steps_identical"] = canonical(a["steps"]) == canonical(b["steps"])
        info["pass"] = bool(info["u238_relative_error"] <= TOL_EXACT and info["unselected_is_u238_only"]
                            and not info["u238_in_no_yields_to_leakage"] and not diffs and not real
                            and info["include_vs_exclude_steps_identical"])
        return info
    out["cases"]["selection"] = guarded(selection)

    def refusals():
        inputs.make_fe56_yield_file(work)
        cases, expect = refusal_cases(inputs)
        stripped = work / "nfy-092_U_235.no_mt459.endf"
        with open(inputs.yield_files["nfy-092_U_235.endf"]["path"], "r", encoding="ascii", errors="replace") as src, \
                open(stripped, "w", encoding="ascii") as dst:
            for line in src:
                if not (len(line.rstrip("\r\n")) >= 75 and line[70:75] == " 8459"):
                    dst.write(line)
        cum = inputs.spec("P153 G4 refusal cumulative without MT=459", flux, {"energy": "spectrum_average",
                                                                              "yields": "cumulative"},
                          files=("nfy-092_U_235.endf",), composition={"U235": N0})
        cum["fission_yields"]["files"] = [{"path": str(stripped), "sha256": sha256_file(stripped)}]
        cases["cumulative_without_mt459"] = cum
        results = {}
        for key, spec in cases.items():
            code, message, wrote = h.refuse(f"g4_refuse_{key}", cand, spec)
            lowered = message.lower()
            named = any(all(word.lower() in lowered for word in group) if key == "include_and_exclude"
                        else any(word.lower() in lowered for word in group) for group in expect[key])
            results[key] = {"exit_code": code, "output_file_written": wrote, "message_tail": message[-300:],
                            "names_cause": named, "pass": bool(code not in (0, None) and not wrote and named)}
        results["pass"] = all(v["pass"] for v in results.values())
        return results
    out["cases"]["refusals"] = guarded(refusals)
    out["pass"] = all(c.get("pass") for c in out["cases"].values())
    return out


def gate_g5(h, cand, inputs):
    flux = inputs.spectra["mix"]

    def run():
        spec = inputs.spec("P153 G5 cumulative", flux, {"energy": "fixed", "fixed_energy_eV": CUMULATIVE_EV,
                                                        "yields": "cumulative"})
        result = h.execute("g5_cumulative", cand, spec)
        info = {"fixed_energy_eV": CUMULATIVE_EV}
        cert = certificate_yields(result)
        info["certificate_yields"] = cert.get("yields")
        info["certificate_parents"] = cert.get("parents")
        yields = {}
        for parent in PARENTS:
            table = inputs.cumulative[parent]
            if table is None:
                raise RuntimeError("the pinned yield file has no MT=459")
            yields[parent] = mixed_yield(table, hats(CUMULATIVE_EV, table["energies"]))
        expected = first_order(inputs.records, [inputs.source_terms(flux, yields)], IRRADIATION_S)[0]
        info["first_order_inventory"] = compare_inventory(result, expected, TOL_FIRST_ORDER)
        overlap = ledger_of(result).get("fission_cumulative_overlap")
        info["overlap"] = {}
        ok_overlap = isinstance(overlap, dict)
        for parent in PARENTS:
            entry = (overlap or {}).get(f"{parent[0]}_{parent[1]}")
            if entry is None and isinstance(overlap, dict):
                entry = next((v for k, v in overlap.items() if parse_parent_key(k) == parent), None)
            row = {"ledger": entry}
            if not isinstance(entry, dict):
                row["pass"] = False
                ok_overlap = False
                info["overlap"][f"{parent[0]}_{parent[1]}"] = row
                continue
            best = None
            for mapping in ("exact",):
                diag = overlap_diagnostic(inputs.records, yields[parent], mapping)
                matches = {
                    "overlap_yield": rel_err(entry.get("overlap_yield"), diag["overlap_yield"]) <= TOL_EXACT,
                    "overlap_products": entry.get("overlap_products") == diag["overlap_products"],
                    "summed_cumulative_yield": any(rel_err(entry.get("summed_cumulative_yield"), diag[k]) <= TOL_EXACT
                                                   for k in ("summed_all_tabulated", "summed_mapped")),
                    "products_with_yield": entry.get("products_with_yield") in (
                        diag["products_with_yield_mapped"], diag["products_with_yield_tabulated"])}
                row[f"recomputed_{mapping}"] = diag
                row[f"matches_{mapping}"] = matches
                if all(matches.values()) and best is None:
                    best = mapping
            share = rel_err(entry.get("overlap_share"),
                            (entry.get("overlap_yield") or 0.0) / entry["summed_cumulative_yield"]
                            if entry.get("summed_cumulative_yield") else None)
            row["share_equals_ratio_of_ledger_values"] = share <= TOL_EXACT
            row["matched_mapping"] = best
            row["pass"] = bool(best and share <= TOL_EXACT)
            ok_overlap = ok_overlap and row["pass"]
            info["overlap"][f"{parent[0]}_{parent[1]}"] = row
        info["overlap_pass"] = ok_overlap
        info["certificate_pass"] = cert.get("yields") == "cumulative"
        info["pass"] = bool(info["first_order_inventory"]["pass"] and ok_overlap and info["certificate_pass"])
        return info
    return guarded(run)


def route_edges(path):
    nuclides, edges = path.get("nuclides"), path.get("edges")
    if not isinstance(nuclides, list) or not isinstance(edges, list):
        raise ValueError("route without nuclides/edges lists")
    return nuclides, edges


def uses_fission_from(path, excluded):
    nuclides, edges = route_edges(path)
    return any(e == "fission" and nuclides[i] in excluded for i, e in enumerate(edges))


def route_key(path):
    return (tuple(path["nuclides"]), tuple(path["edges"]))


def gate_g6(h, cand, inputs):
    flux = inputs.spectra["mix"]
    schedule = [{"dt": "1 d", "flux": 1.0}]
    pathways = {"targets": ["Mo99", "Xe135", "Cs137", "Ba140", "Zr95", "I131"], "path_floor": 1e-4, "max_depth": 8,
                "max_paths": 1000}

    def run():
        free = h.execute("g6_unfiltered", cand, inputs.spec("P153 G6 unfiltered", flux,
                                                            {"energy": "spectrum_average"}, schedule=schedule,
                                                            pathways=dict(pathways)))
        filt = h.execute("g6_filtered", cand, inputs.spec("P153 G6 fission_parents U235", flux,
                                                          {"energy": "spectrum_average"}, schedule=schedule,
                                                          pathways=dict(pathways, fission_parents=["U235"])))
        info = {"steps_identical": canonical(free["steps"]) == canonical(filt["steps"]), "targets": {}}
        ft = {t["target"]: t for t in free["pathway_routes"]["targets"]}
        gt = {t["target"]: t for t in filt["pathway_routes"]["targets"]}
        ok = info["steps_identical"] and bool(gt)
        info["targets_in_both"] = sorted(set(ft) & set(gt))
        info["targets_missing"] = sorted(set(pathways["targets"]) - (set(ft) & set(gt)))
        removed_total, violations_total, u235_fission_routes = 0, 0, 0
        for name in info["targets_in_both"]:
            a, b = ft[name], gt[name]
            keys_a = {route_key(p): p for p in a["paths"]}
            keys_b = {route_key(p): p for p in b["paths"]}
            violations = [p["nuclides"] for p in b["paths"] if uses_fission_from(p, {"U238"})]
            removed = [keys_a[k] for k in keys_a if k not in keys_b]
            added = [k for k in keys_b if k not in keys_a]
            bad_removed = [p["nuclides"] for p in removed if not uses_fission_from(p, {"U238"})]
            changed = [k for k in keys_b if k in keys_a
                       and rel_err(keys_b[k]["atoms_per_g"], keys_a[k]["atoms_per_g"]) > 1e-9]
            expected_residual = a["total_atoms_per_g"] - math.fsum(p["atoms_per_g"] for p in b["paths"])
            row = {"routes_unfiltered": len(a["paths"]), "routes_filtered": len(b["paths"]),
                   "routes_removed": len(removed), "routes_with_u238_fission_in_filtered": len(violations),
                   "removed_without_u238_fission_edge": bad_removed[:5], "routes_added_by_filter": len(added),
                   "surviving_routes_changed": len(changed),
                   "paths_dropped_by_cap": [a.get("paths_dropped_by_cap"), b.get("paths_dropped_by_cap")],
                   "total_equal": rel_err(b["total_atoms_per_g"], a["total_atoms_per_g"]) <= TOL_EXACT,
                   "residual_unfiltered": a["residual_atoms_per_g"], "residual_filtered": b["residual_atoms_per_g"],
                   "residual_expected": expected_residual,
                   "removed_atoms_per_g": math.fsum(p["atoms_per_g"] for p in removed),
                   "explained_fraction": [a.get("explained_fraction"), b.get("explained_fraction")]}
            row["residual_relative_to_total"] = abs(b["residual_atoms_per_g"] - expected_residual) \
                / max(a["total_atoms_per_g"], 1e-300)
            capped = bool(a.get("paths_dropped_by_cap")) or bool(b.get("paths_dropped_by_cap"))
            row["pass"] = bool(not violations and not bad_removed and not added and not changed and row["total_equal"]
                               and row["residual_relative_to_total"] <= 1e-9 and not capped)
            removed_total += len(removed)
            violations_total += len(violations)
            u235_fission_routes += sum(1 for p in b["paths"] if uses_fission_from(p, {"U235"}))
            ok = ok and row["pass"]
            info["targets"][name] = row
        info["routes_removed_in_total"] = removed_total
        info["u235_fission_routes_kept"] = u235_fission_routes
        info["vacuous"] = removed_total == 0 or u235_fission_routes == 0
        info["pass"] = bool(ok and not info["vacuous"] and not info["targets_missing"] and violations_total == 0)
        return info
    return guarded(run)


def gate_g0():
    digest = sha256_file(PROTOCOL)
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    hit = [line for line in lines if line.startswith(digest) and line.split()[-1] == f"protocols/{PROTOCOL.name}"]
    return {"protocol_sha256": digest, "in_protocol_hash_txt": bool(hit), "external": True,
            "note": "recorded for information; G0 is evaluated by the coordinator"}


# ------------------------------------------------------------------------------ self-check


def endf_number(x):
    if x == 0:
        return "0.000000+0".rjust(11)
    mantissa, exponent = f"{x:.6e}".split("e")
    return f"{mantissa}{'+' if int(exponent) >= 0 else '-'}{abs(int(exponent))}".rjust(11)


def endf_line(fields, mat, mf, mt, ns):
    cells = "".join(endf_number(v) if isinstance(v, float) else str(v).rjust(11) for v in fields)
    return f"{cells:<66}{mat:4d}{mf:2d}{mt:3d}{ns:5d}"


def self_check():
    import tempfile
    # hat functions
    grid = [0.0253, 5e5, 1.4e7]
    assert hats(1e-3, grid) == [1.0, 0.0, 0.0] and hats(2e7, grid) == [0.0, 0.0, 1.0]
    assert hats(5e5, grid) == [0.0, 1.0, 0.0] or hats(5e5, grid)[1] == 1.0
    mid = hats(7.25e6, grid)
    w = (7.25e6 - 5e5) / (1.4e7 - 5e5)
    assert mid[0] == 0.0 and abs(mid[1] - (1 - w)) < 1e-15 and abs(mid[2] - w) < 1e-15
    assert abs(math.fsum(hats(123456.0, grid)) - 1.0) < 1e-15
    assert hats(3.0, [1.0]) == [1.0]
    # Maxwellian group integration on a fine geometric grid: unit total, mean energy 2 kT
    bounds = [1e-5 * (1.0 + 0.01) ** i for i in range(2600)]
    assert bounds[-1] > 40 * KT_EV
    groups = maxwellian_groups(bounds, KT_EV)
    assert abs(math.fsum(groups) - 1.0) < 1e-6, math.fsum(groups)
    mean = math.fsum(g * e for g, e in zip(groups, group_midpoints(bounds)))
    assert abs(mean / (2 * KT_EV) - 1.0) < 1e-3, mean / KT_EV
    assert all(g >= -1e-16 for g in groups)
    # rate weights: sums to one; one group reproduces the interpolation at its midpoint; no rate -> None
    b = [1e-5, 1.0, 1e3, 1e6, 3e6, 1.6e7]
    sigma = [1.0, 2.0, 3.0, 4.0, 5.0]
    wts = rate_weights(sigma, [0.0, 0.0, 0.0, 7.0, 0.0], b, grid)
    assert abs(math.fsum(wts) - 1.0) < 1e-15
    assert max(abs(x - y) for x, y in zip(wts, hats(math.sqrt(1e6 * 3e6), grid))) < 1e-15
    assert rate_weights(sigma, [0.0] * 5, b, grid) is None
    two = rate_weights([1.0, 1.0, 1.0, 1.0, 1.0], [1.0, 0.0, 0.0, 0.0, 3.0], b, grid)
    h1, h2 = hats(math.sqrt(1e-5 * 1.0), grid), hats(math.sqrt(3e6 * 1.6e7), grid)
    assert max(abs(two[k] - (0.25 * h1[k] + 0.75 * h2[k])) for k in range(3)) < 1e-15
    # ENDF MF=8 reader round trip on a synthetic two-energy MT=454 and MT=459 section
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nfy.endf"
        rows = []
        for mt, scale in ((454, 1.0), (459, 2.0)):
            ns = 1
            sec = [endf_line([92235.0, 233.0, 2, 0, 0, 0], 9228, 8, mt, ns)]
            for e, ys in ((0.0253, (0.02, 0.5)), (1.4e7, (0.04, 0.4))):
                ns += 1
                sec.append(endf_line([e, 0.0, 2, 0, 8, 2], 9228, 8, mt, ns))
                ns += 1
                sec.append(endf_line([52135.0, 0.0, ys[0] * scale, 0.001, 53135.0, 0.0], 9228, 8, mt, ns))
                ns += 1
                sec.append(endf_line([ys[1] * scale, 0.002, 0.0, 0.0, 0.0, 0.0], 9228, 8, mt, ns))
            rows += sec
        path.write_text("\n".join(rows) + "\n")
        t = read_yield_file(path, 454)
        assert t["za"] == 92235 and t["energies"] == [0.0253, 1.4e7] and t["tables"][1][(52135, 0)][0] == 0.04
        assert t["tables"][0][(53135, 0)][0] == 0.5
        assert abs(read_yield_file(path, 459)["tables"][0][(52135, 0)][0] - 0.04) < 1e-12
        assert read_yield_file(path, 455) is None
        mix = mixed_yield(t, [0.75, 0.25])
        assert abs(mix[(52135, 0)] - (0.75 * 0.02 + 0.25 * 0.04)) < 1e-15
    # overlap on a synthetic chain Te135 -> I135 -> Xe135 (stable) plus an unrelated Mo99 with a state, and key parsing
    def rec(za, half, modes, nst=0):
        return {"za": za, "liso": 0, "awr": 1.0, "nst": nst, "half_life": half, "modes": modes}
    records = {(52135, 0): rec(52135, 19.0, [(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]),
               (53135, 0): rec(53135, 23652.0, [(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]),
               (54135, 0): rec(54135, 3.3e4, [(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)]),
               (55135, 0): rec(55135, 1e13, [], nst=1),
               (42099, 0): rec(42099, 2e5, [(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)])}
    records[(43099, 0)] = rec(43099, 1e12, [], nst=1)
    yields = {(52135, 0): 0.05, (53135, 0): 0.03, (54135, 0): 0.001, (42099, 0): 0.01, (99999, 0): 0.02,
              (53135, 1): 0.004}
    d = overlap_diagnostic(records, yields, "resolved")
    # overlapping: I135 (fed by Te135) and Xe135 (fed by I135); Mo99 has no yielding precursor. (53135,1) resolves to
    # the ground state I135 and adds to its yield.
    assert d["overlap_products"] == 2 and abs(d["overlap_yield"] - (0.03 + 0.004 + 0.001)) < 1e-15, d
    assert d["products_with_yield_mapped"] == 4 and d["products_with_yield_tabulated"] == 6
    assert abs(d["summed_all_tabulated"] - 0.115) < 1e-15 and abs(d["summed_mapped"] - 0.095) < 1e-15
    de = overlap_diagnostic(records, yields, "exact")
    assert abs(de["overlap_yield"] - (0.03 + 0.001)) < 1e-15 and de["products_with_yield_mapped"] == 4
    assert overlap_diagnostic(records, {(52135, 0): 1.0, (42099, 0): 1.0}, "resolved")["overlap_products"] == 0
    assert parse_parent_key("92235_0") == (92235, 0) and parse_parent_key("92_238") == (92238, 0)
    assert parse_parent_key("U235") == (92235, 0) and parse_parent_key("Am242m1") == (95242, 1)
    assert parse_parent_key("nonsense") is None
    # first-order solution against the analytic Bateman result (Te135 -> I135, constant source into Te135)
    try:
        import scipy  # noqa: F401
        lam_a, lam_b = decay_edges(records, (52135, 0))["lam"], decay_edges(records, (53135, 0))["lam"]
        s, tt = 1.0e9, 600.0
        got = first_order(records, [{(52135, 0): s}], tt)[0]
        na = s * (1 - math.exp(-lam_a * tt)) / lam_a
        # closed form with decay of B: N_b = S/lam_b - S/lam_b * (lam_a e^{-lam_b t} - lam_b e^{-lam_a t})/(lam_a - lam_b)
        nb = s / lam_b * (1.0 - (lam_a * math.exp(-lam_b * tt) - lam_b * math.exp(-lam_a * tt)) / (lam_a - lam_b))
        assert abs(got[(52135, 0)] - na) / na < 1e-9, (got[(52135, 0)], na)
        assert abs(got[(53135, 0)] - nb) / nb < 1e-9, (got[(53135, 0)], nb)
    except ImportError:
        print("P153 self-check: scipy missing, first-order test skipped")
    # inventory comparison and difference helpers
    run = {"steps": [{"inventory": [{"nuclide": "I135", "atoms_per_g": 2.0}]}]}
    assert compare_inventory(run, {(53135, 0): 2.0}, 1e-6)["pass"]
    assert not compare_inventory(run, {(53135, 0): 2.1}, 1e-6)["pass"]
    assert not compare_inventory(run, {(54135, 0): 2.0}, 1e-6)["pass"]
    assert numeric_diff({"a": [1.0, 2.0]}, {"a": [1.0, 2.0 * (1 + 1e-13)]}, 1e-12) == []
    assert numeric_diff({"a": 1.0}, {"a": 1.1}, 1e-12)
    assert is_echo("/certificate/fission_yields/parents: 1 vs 2") and not is_echo("/steps[0]/inventory[3]/atoms: 1 vs 2")
    entries = find_parent_entries({"x": {"92235_0": {"requested_energy_eV": 1.0}},
                                   "y": [{"parent": "92_238", "requested_energy_eV": 2.0}]}, "requested_energy_eV")
    assert set(entries) == {(92235, 0), (92238, 0)}
    assert uses_fission_from({"nuclides": ["U238", "Cs137"], "edges": ["fission"]}, {"U238"})
    assert not uses_fission_from({"nuclides": ["U238", "U239"], "edges": ["(n,g)"]}, {"U238"})
    print("P153 self-check: PASS")
    return 0


# ------------------------------------------------------------------------------ main


def find_data_root(data_dir):
    candidate = Path(data_dir).expanduser()
    if candidate.is_absolute():
        return candidate, candidate.parent.parent
    for base in [ROOT] + list(ROOT.parents):
        if (base / candidate).is_dir():
            return base / candidate, base
    raise RuntimeError(f"data directory {data_dir} not found under {ROOT} or its parents")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--workdir", default="target/p153-work")
    ap.add_argument("--out", default="results/p153_fission_yields.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    ap.add_argument("--data-dir", default="actinv-data/v1.1.0")
    ap.add_argument("--nfpy-dir", default="~/nuclear-data/endfb-viii.0-nfpy")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
    ref, cand = str(Path(args.reference).resolve()), str(Path(args.candidate).resolve())
    work = Path(args.workdir)
    work = (work if work.is_absolute() else ROOT / work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    data_dir, data_base = find_data_root(args.data_dir)
    cwd = ROOT if (ROOT / "actinv-data").exists() else data_base
    h = Harness(work, args.timeout, args.reuse, cwd)
    inputs = Inputs(data_dir, Path(args.nfpy_dir).expanduser())

    record = {"protocol": "protocols/ACTINV-P153_PROTOCOL.md", "protocol_sha256": sha256_file(PROTOCOL),
              "checker_sha256": sha256_file(__file__), "reference_sha256": sha256_file(ref),
              "candidate_sha256": sha256_file(cand),
              "data": {"library": {"path": str(inputs.library_path), "sha256": inputs.library_sha256},
                       "decay_primary": {"path": str(inputs.decay_primary), "sha256": sha256_file(inputs.decay_primary)},
                       "decay_fallback": {"path": str(inputs.decay_fallback),
                                          "sha256": sha256_file(inputs.decay_fallback)},
                       "yields": inputs.yield_files},
              "design": {"U235_fission_rate_per_atom_per_s": RATE_U235, "atoms_per_g_each_parent": N0,
                         "irradiation_s": IRRADIATION_S, "kT_eV": KT_EV,
                         "tabulated_energies_eV": {str(p[0]): inputs.tables[p]["energies"] for p in PARENTS},
                         "fission_rates_per_atom_per_s": {name: {str(p[0]): inputs.rate(p, flux) for p in PARENTS}
                                                          for name, flux in inputs.spectra.items()},
                         "tolerances": {"weights": TOL_WEIGHT, "first_order_inventory": TOL_FIRST_ORDER,
                                        "exact": TOL_EXACT}},
              "external_gates": {"G0": gate_g0(), "G1": "fmt, clippy -D warnings, workspace tests: coordinator",
                                 "G7": "CI on the pushed commit and committed baselines: coordinator"},
              "gates": {}}
    gates = record["gates"]
    gates["G2"] = guarded(lambda: gate_g2(h, ref, cand, inputs, cwd))
    gates["G3"] = guarded(lambda: gate_g3(h, cand, inputs))
    gates["G4"] = guarded(lambda: gate_g4(h, cand, inputs, work))
    gates["G5"] = gate_g5(h, cand, inputs)
    gates["G6"] = gate_g6(h, cand, inputs)
    record["runs"] = h.runs
    judged = ("G2", "G3", "G4", "G5", "G6")
    overall = all(gates[g].get("pass") is True for g in judged)
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in judged:
        print(f"P153 {g}: {'PASS' if gates[g].get('pass') else 'FAIL'}")
        if "error" in gates[g]:
            print(f"  {g}: ERROR {gates[g]['error']}")
        for label, case in (gates[g].get("cases") or {}).items():
            if isinstance(case, dict):
                print(f"  {g}/{label}: {'PASS' if case.get('pass') else 'FAIL'}"
                      + (f" ERROR {case['error']}" if "error" in case else ""))
    top = (gates["G3"].get("cases", {}).get("mix") or {}).get("information_top5_fission_product_activity_vs_spectrum_average")
    if top:
        print("  G3 information, mixed spectrum, rate_weighted vs spectrum_average (largest FP activities):")
        for row in top:
            print(f"    {row['nuclide']}: {row['relative_difference']}")
    print("P153 G0, G1, G7: external (not evaluated here)")
    print(f"P153: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
