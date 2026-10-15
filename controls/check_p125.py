#!/usr/bin/env python3
"""P125 gates G0, G2-G5 (protocols/ACTINV-P125_PROTOCOL.md): spontaneous-fission products, fission count and burn-up,
spontaneous-fission neutron source.

  G0  The SHA-256 of the protocol file is a line of protocols/protocol_hash.txt.
  G2  Unchanged by default. `examples/fns_fe_5min.json` and a U-235 irradiation with a fission-yield file (no P125
      option) give byte-identical results from the reference and candidate binaries once the wall-clock keys are
      dropped (top-level `ms`; `wall_time_s` and `cells_per_s` wherever they occur).
  G3  Fission count.
      (a) A U-235 sample (1e21 atoms/g, coupled mode, three steps: irradiation, decay gap, irradiation at twice the
          flux) in the FNS 709-group spectrum with `options.fission` and the ENDF/B-VIII.0 nfpy U-235 yield file.
          Induced fissions at every step end equal sum_i r_i * integral(N_i dt) recomputed here:
          * the one-group MT=18 rate of each fissionable nuclide is streamed from sig.npy rows (not `inspect xs`);
          * U-235: N(t) and its integral come from an independent exact small network (U-235, U-235m1 returning by
            isomeric transition, Pa-235 returning by beta decay) solved with scipy.linalg.expm. Every reaction row
            of the target is a removal (the loss rows, ZAP = -1), exactly as the library defines the matrix; the
            production rows into the three states and the decay branches from the ENDF/B-VIII.0 file close the loop.
            A removal rate alone would be wrong: the library's MT=4 loss row is the isomer production row itself.
          * every other fissionable nuclide present in the run inventory (second-generation U-234, U-236, ...) adds
            r_i * trapezoid(N_i at the step boundaries from the run inventory) * dt. The sample is sized for a total
            optical depth near 0.003, so these terms are of order 1e-4 of the total and the trapezoid error is below
            1e-6 of it; the share and the bound are reported.
          Tolerance 1e-6 relative. The burn-up percentage and MWd/kg follow from the emitted fission count by the
          documented arithmetic (1e-12), and fissions = induced + spontaneous.
      (b) A decay-only Cf-252 sample (no library, no yield file, `fission_yields.spontaneous.nubar_endf` only, plus
          1e13 atoms/g of Cm-250, an SF emitter with no nu-bar in the file, to exercise the uncovered list):
          spontaneous fissions at every step end equal an independent exact solve of the Cf-252 decay chain with
          counter states (mpmath, 60 digits) within 1e-9. The closed form N0 (1 - exp(-lambda t)) BR_SF applies to
          the first step (30 min) only, where the daughters' fissions are below 1e-9 of the total; it is checked there and
          tabulated for later steps with the size of the daughter contribution.
  G4  SF neutron source: for every step, sum over populated states of lambda * BR_SF * N * nu-bar from the run
      inventory and this script's own ENDF-6 MF=1/MT=452 parse (Cf-252: 3.7676), within 1e-12; the Cf-252 entry
      alone; the uncovered decay rate; and the coverage lists (every populated SF state without nu-bar is listed).
  G5  SF products: data-limited while no ENDF/B spontaneous-fission yield sublibrary is installed (reported with the
      search that found none).
  G1 (fmt, clippy, tests) and G6 (CI, committed baselines) are run by the coordinator and are not evaluated here.

Overall PASS iff G0, G2, G3 and G4 pass. Runs are serial, without a shell, each under a timeout.

Usage:
  check_p125.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p125-work]
      [--out results/p125_fission.json] [--timeout 3600] [--reuse]
      [--data-dir actinv-data/v1.1.0] [--nfpy-u235 ~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf]

Run inside the required systemd cgroup (the U-235 run carries several thousand states). Needs numpy, scipy and
mpmath for the independent solvers. Exit 0 on PASS, 1 on FAIL.
"""
import argparse
import array
import ast
import copy
import hashlib
import json
import math
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# Shared ENDF-6 field readers, timing-key stripping and difference listing of the P122 checker.
from check_p122 import (absolutize_paths, canonical, cont, endf_float, first_differences, read_list,  # noqa: E402
                        sha256_file, strip_timing)

ROOT = HERE.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P125_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
FNS_SPEC = ROOT / "examples" / "fns_fe_5min.json"
LIBRARY_NAME = "tendl-2025-neutron-709g.npz"
DECAY_PRIMARY_NAME = "endf-b-viii-0_decay.dat"
DECAY_FALLBACK_NAME = "jeff-3-3_decay.dat"

# Physical constants restated from the engine (crates/actinv-data/src/composition.rs, run.rs, fission_count.rs).
NA = 6.02214076e23
NEUTRON_MASS_AMU = 1.00866491595
EV_J = 1.602176634e-19
J_PER_MWD = 86_400.0e6
BARN_CM2 = 1.0e-24
BRANCHING_TOLERANCE = 1e-5  # chain.rs
HEAVY_METAL_MIN_Z = 90  # fission_count.rs

U235 = (92235, 0)
CF252 = (98252, 0)
N0_U235 = 1.0e21  # atoms per gram, declared literally (basis atoms_per_g)
N0_CF252 = 1.0e21
CM250 = (96250, 0)
N0_CM250 = 1.0e13  # a trace of an SF emitter with no nu-bar in the ENDF/B-VIII.0 file, to exercise the uncovered list
TARGET_OPTICAL_DEPTH = 0.003  # total U-235 removal over the weighted schedule
ENERGY_PER_FISSION_MEV = 200.0
CF252_NUBAR_PROTOCOL = 3.7676

TOL_INDUCED = 1e-6
TOL_SF_COUNT = 1e-9
TOL_SF_CLOSED_FORM = 1e-9
TOL_ARITHMETIC = 1e-12
TOL_NEUTRONS = 1e-12
TOL_MODEL_INVENTORY = 1e-7  # independent U-235 network against the run inventory (diagnostic gate)
MAX_LISTED = 20

STEP_OF = {1: (1, 0), 2: (-1, 0), 3: (0, 0), 4: (-2, -4), 5: (0, -1), 7: (-1, -1)}  # chain.rs step_of


def rel_err(got, exp):
    """Relative error; infinite when either side is not a finite number. Zero expected demands zero."""
    if got is None or exp is None or not (math.isfinite(got) and math.isfinite(exp)):
        return math.inf
    if exp == 0.0:
        return 0.0 if got == 0.0 else math.inf
    return abs(got - exp) / abs(exp)


# ------------------------------------------------------------------------------ ENDF-6 decay sublibrary


def scan_decay_file(path):
    """One streaming pass over an ENDF-6 decay sublibrary. Returns (records by (ZA, LISO), nubar by (ZA, LISO)).
    A record holds AWR, NST, half life, and the decay modes as (RTYP, RFS, Q, DQ, BR, DBR). The nu-bar is the
    spontaneous-fission value of MF=1/MT=452 (else MT=456) at E = 0, as the engine reads it: the polynomial's constant
    term (LNU = 1) or the first tabulated point (LNU = 2); the state comes from the MF=8/MT=457 HEAD (L2)."""
    sections = {}
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            tag = line[70:75]
            if tag in (" 8457", " 1452", " 1456"):
                sections.setdefault(line[66:70], {}).setdefault(tag.strip(), []).append(line)
    records, nubar, duplicates = {}, {}, []
    for mat, parts in sections.items():
        if "8457" not in parts:
            continue
        lines = parts["8457"]
        za, awr, _l1, liso, nst, _n2 = cont(lines[0])
        energy_head, energies, nxt = read_list(lines, 1)
        mode_head, mode_values, _ = read_list(lines, nxt)
        ndk = mode_head[5]
        modes = [tuple(mode_values[6 * k:6 * k + 6]) for k in range(ndk)]
        key = (int(round(za)), liso)
        if key in records:
            duplicates.append(key)
        records[key] = {"za": key[0], "liso": liso, "awr": awr, "nst": nst, "half_life": energy_head[0],
                        "modes": modes, "mat": mat}
        for tag, mt in (("1452", 452), ("1456", 456)):
            if tag in parts:
                nubar[key] = parse_nubar(parts[tag], mt)
                break
    return records, nubar, duplicates


def parse_nubar(lines, mt):
    """(nu-bar at E = 0, basis string, MT) from one MF=1 section's lines."""
    _za, _awr, _l1, lnu, _n1, _n2 = cont(lines[0])
    if lnu == 1:
        _head, values, _ = read_list(lines, 1)
        return values[0], "LNU=1 polynomial C0", mt
    if lnu == 2:
        head = cont(lines[1])
        nr, npts = head[4], head[5]
        words, index = [], 2
        while len(words) < 2 * nr + 2 * npts:
            line = lines[index]
            words.extend(line[11 * k:11 * (k + 1)] for k in range(6))
            index += 1
        return endf_float(words[2 * nr + 1]), "LNU=2 table first point", mt
    raise ValueError(f"unsupported LNU {lnu}")


def merge_decay(primary, fallback):
    """The engine merges the primary with the fallback: the primary wins, the fallback supplies only absent keys."""
    p_records, p_nubar, p_dups = scan_decay_file(primary)
    records = dict(p_records)
    from_fallback = 0
    if fallback:
        f_records, _f_nubar, _f_dups = scan_decay_file(fallback)
        for key, rec in f_records.items():
            if key not in records:
                records[key] = rec
                from_fallback += 1
    return records, p_nubar, {"primary_keys": len(p_records), "from_fallback": from_fallback,
                              "primary_duplicates": [list(k) for k in p_dups[:MAX_LISTED]]}


def lam_of(rec):
    """Decay constant as Nuclide::lambda: zero for a stable evaluation (NST = 1) or a non-positive half life."""
    if rec is None or rec["nst"] == 1 or rec["half_life"] <= 0.0:
        return 0.0
    return math.log(2.0) / rec["half_life"]


def rtyp_digits(rtyp):
    """chain.rs rtyp_digits: the decimal digits of RTYP printed to six places, trailing zeros trimmed."""
    text = f"{rtyp:.6f}".rstrip("0").rstrip(".")
    return [int(c) for c in text if c.isdigit()]


def decay_edges(records, key):
    """Decay of one state as chain.rs builds it. Returns lambda, daughters {key: rate}, the spontaneous-fission rate
    (every digit-6 mode, whether or not products exist) and the rate booked to leakage."""
    rec = records[key]
    lam = lam_of(rec)
    out = {"lam": lam, "edges": {}, "sf": 0.0, "leak": 0.0, "br_sf": 0.0}
    if lam == 0.0:
        return out
    assigned = 0.0
    for mode in rec["modes"]:
        if mode[4] > 0.0:
            assigned += mode[4]
    scale = 1.0
    if abs(assigned - 1.0) > BRANCHING_TOLERANCE:
        if assigned < 1.0:
            out["leak"] += lam * (1.0 - assigned)
        else:
            scale = 1.0 / assigned
    z, a = rec["za"] // 1000, rec["za"] % 1000
    for rtyp, rfs, _q, _dq, br, _dbr in rec["modes"]:
        if br <= 0.0:
            continue
        rate = lam * br * scale
        zz, aa, bad = z, a, False
        for digit in rtyp_digits(rtyp):
            if digit == 6:
                out["sf"] += rate
                out["leak"] += rate  # no yield file in this checker's runs: the branch stays on leakage
                bad = True
                break
            if digit not in STEP_OF:
                out["leak"] += rate
                bad = True
                break
            zz += STEP_OF[digit][0]
            aa += STEP_OF[digit][1]
        if bad:
            continue
        want = (zz * 1000 + aa, int(math.floor(rfs + 0.5)))
        ground = (zz * 1000 + aa, 0)
        daughter = want if want in records else (ground if ground in records else None)
        if daughter is None or daughter == key:
            out["leak"] += rate
        else:
            out["edges"][daughter] = out["edges"].get(daughter, 0.0) + rate
    out["br_sf"] = out["sf"] / lam
    return out


# ------------------------------------------------------------------------------ activation library (streamed)


def read_npy_header(handle):
    if handle.read(6) != b"\x93NUMPY":
        raise ValueError("not an npy member")
    major, _minor = handle.read(2)
    if major == 1:
        (length,) = struct.unpack("<H", handle.read(2))
    elif major in (2, 3):
        (length,) = struct.unpack("<I", handle.read(4))
    else:
        raise ValueError(f"unsupported npy version {major}")
    return ast.literal_eval(handle.read(length).decode("latin1").strip())


def read_exact(handle, count):
    buffer = bytearray()
    while len(buffer) < count:
        chunk = handle.read(count - len(buffer))
        if not chunk:
            raise ValueError("npy member is truncated")
        buffer += chunk
    return bytes(buffer)


def skip_bytes(handle, count):
    while count > 0:
        step = min(count, 1 << 24)
        read_exact(handle, step)
        count -= step


class Library:
    """Activation library read independently of the engine: bounds, row descriptors, and cross-section rows streamed
    one pass at a time (the 950 MB sig.npy is never held in memory)."""

    def __init__(self, npz_path, index_path=None):
        assert sys.byteorder == "little"
        self.path = str(npz_path)
        index_path = index_path or str(npz_path).replace(".npz", "_index.json")
        self.index_path = str(index_path)
        index = json.loads(Path(index_path).read_text())
        self.targets = [(t["za"], t["liso"]) for t in index["targets"]]
        self.target_index = {key: i for i, key in enumerate(self.targets)}
        with zipfile.ZipFile(self.path) as archive:
            with archive.open("bounds.npy") as handle:
                header = read_npy_header(handle)
                values = array.array("d")
                values.frombytes(read_exact(handle, 8 * header["shape"][0]))
                self.bounds = list(values)
            with archive.open("rows.npy") as handle:
                header = read_npy_header(handle)
                assert header["descr"] == "<i8" and header["shape"][1] == 5
                values = array.array("q")
                values.frombytes(read_exact(handle, 8 * header["shape"][0] * 5))
        self.rows = [tuple(values[5 * i:5 * i + 5]) for i in range(header["shape"][0])]  # target, mt, zap, lfs, lmf
        self.by_target = {}
        for i, row in enumerate(self.rows):
            self.by_target.setdefault(row[0], []).append(i)
        self.n_groups = len(self.bounds) - 1

    def stream(self, wanted):
        """{row index: group cross sections in barns} for the requested rows, in one pass over sig.npy."""
        out = {}
        with zipfile.ZipFile(self.path) as archive, archive.open("sig.npy") as handle:
            header = read_npy_header(handle)
            assert header["descr"] == "<f8" and not header["fortran_order"]
            assert header["shape"] == (len(self.rows), self.n_groups)
            row_bytes = 8 * self.n_groups
            position = 0
            for row in sorted(set(wanted)):
                skip_bytes(handle, (row - position) * row_bytes)
                values = array.array("d")
                values.frombytes(read_exact(handle, row_bytes))
                out[row] = list(values)
                position = row + 1
        return out

    def rows_of(self, key):
        target = self.target_index.get(key)
        return [] if target is None else self.by_target.get(target, [])


def ascending_flux(spectrum):
    """Spec::ascending_flux: reverse when `descending`, then scale to `total` (naive left-to-right sums)."""
    flux = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending"):
        flux.reverse()
    total = spectrum.get("total")
    if total is not None:
        s = 0.0
        for v in flux:
            s += v
        if s > 0.0:
            k = total / s
            flux = [v * k for v in flux]
    return flux


def fold(sigma, flux):
    """Sum of sigma_g * flux_g in barn * particles cm^-2 s^-1, as the engine's collapse numerator."""
    acc = 0.0
    for s, f in zip(sigma, flux):
        acc += s * f
    return acc * BARN_CM2


# ------------------------------------------------------------------------------ U-235 network (G3a)


def u235_plan(lib, records, flux):
    """Rates at unit flux multiplier for the U-235 target, from its streamed library rows."""
    rows = lib.rows_of(U235)
    sig = lib.stream(rows)
    loss, fission = 0.0, 0.0
    products = {}  # (zap, lfs) -> rate, rows with a product (ZAP > 0)
    unmapped = 0.0
    for i in rows:
        _t, mt, zap, lfs, lmf = lib.rows[i]
        rate = fold(sig[i], flux)
        if zap == -1:
            loss += rate
        elif mt == 18 and zap == 0:
            fission += rate
        elif lmf == -2:
            unmapped += rate
        elif zap > 0:
            products[(zap, lfs)] = products.get((zap, lfs), 0.0) + rate
    # the other network states are tracked for their fissions only (their own removal by reactions is orders of
    # magnitude below their decay constants)
    state_fission = [fission, 0.0, 0.0]
    for position, key in enumerate(NETWORK_STATES[1:], start=1):
        state_rows = [i for i in lib.rows_of(key) if lib.rows[i][1] == 18 and lib.rows[i][2] == 0]
        state_sig = lib.stream(state_rows)
        state_fission[position] = sum(fold(state_sig[i], flux) for i in state_rows)
    return {"n_rows": len(rows), "loss_rate_per_s": loss, "fission_rate_per_s": fission, "products": products,
            "unmapped_rate_per_s": unmapped, "lambda_per_s": lam_of(records[U235]),
            "removal_rate_per_s": loss + lam_of(records[U235]), "state_fission_rate_per_s": state_fission}


def resolve_product(records, zap, lfs):
    """The state a library product row lands on: (ZAP, LFS), else the ground state, else leakage (None)."""
    if (zap, lfs) in records:
        return (zap, lfs)
    if (zap, 0) in records:
        return (zap, 0)
    return None


NETWORK_STATES = [U235, (92235, 1), (91235, 0)]


def u235_network(records, plan):
    """Exact small network around U-235: ground state A, isomer B, Pa-235 C. Returns per-multiplier removal and
    coupling coefficients. States outside the network are sinks; their return paths (U-236 (n,2n), ...) are second
    order and bounded in the report."""
    n = len(NETWORK_STATES)
    index = {key: i for i, key in enumerate(NETWORK_STATES)}
    decay = [[0.0] * n for _ in range(n)]  # decay[row][col]
    lam = [0.0] * n
    for col, key in enumerate(NETWORK_STATES):
        if key not in records:
            continue
        info = decay_edges(records, key)
        lam[col] = info["lam"]
        for daughter, rate in info["edges"].items():
            if daughter in index:
                decay[index[daughter]][col] += rate
    react_loss = [plan["loss_rate_per_s"], 0.0, 0.0]  # only the ground state has library rows here
    react = [[0.0] * n for _ in range(n)]
    for (zap, lfs), rate in plan["products"].items():
        target = resolve_product(records, zap, lfs)
        if target in index:
            react[index[target]][0] += rate
    return {"index": index, "lam": lam, "decay": decay, "react_loss": react_loss, "react": react}


def u235_integrate(network, schedule, n0):
    """Forward solve through (dt_s, multiplier) steps. Returns per-step end populations [A, B, C] and the integral of
    each state's population over the step (atoms s per gram)."""
    import numpy as np
    from scipy.linalg import expm
    n = len(NETWORK_STATES)
    state = np.zeros(n)
    state[0] = n0
    out = []
    for dt, multiplier in schedule:
        matrix = np.zeros((2 * n, 2 * n))
        for col in range(n):
            matrix[col, col] -= network["lam"][col] + multiplier * network["react_loss"][col]
            for row in range(n):
                matrix[row, col] += network["decay"][row][col] + multiplier * network["react"][row][col]
            matrix[n + col, col] = 1.0  # d(integral_i)/dt = N_i
        end = expm(matrix * dt) @ np.concatenate([state, np.zeros(n)])
        state = end[:n].copy()
        out.append({"end": state.tolist(), "integrals": end[n:].tolist()})
    return out


def sizing(plan, schedule_days):
    """Flux multiplier giving TARGET_OPTICAL_DEPTH over the weighted schedule, rounded to one significant figure."""
    weighted_s = sum(days * 86400.0 * weight for days, weight in schedule_days)
    return float(f"{TARGET_OPTICAL_DEPTH / (plan['removal_rate_per_s'] * weighted_s):.1g}")


def u235_spec(base, library, decay, yields, multiplier, with_count):
    spec = copy.deepcopy(base)
    spec["title"] = "P125 G3a: U-235 in the FNS spectrum, fission count"
    spec["library"] = {"path": library["path"], "sha256": library["sha256"]}
    spec["decay"] = {"primary": decay["primary"]["path"], "fallback": decay["fallback"]["path"]}
    spec["material"] = {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"U235": N0_U235}}
    spec["fission_yields"] = {"files": [{"path": yields["path"], "sha256": yields["sha256"]}]}
    spec["options"] = {"mode": "coupled", "prune": "rate", "bmin_atoms_per_g": 1e-8, "temperature_K": 293.6}
    if with_count:
        spec["options"]["fission"] = {"energy_per_fission_MeV": ENERGY_PER_FISSION_MEV}
    spec["schedule"] = [{"dt": "10 d", "flux": multiplier}, {"dt": "3 d", "flux": 0.0},
                        {"dt": "20 d", "flux": 2.0 * multiplier}]
    return spec


U235_SCHEDULE = [(10.0 * 86400.0, 1.0), (3.0 * 86400.0, 0.0), (20.0 * 86400.0, 2.0)]  # (dt_s, weight on the multiplier)
U235_SIZING_DAYS = [(10.0, 1.0), (20.0, 2.0)]


def inventory_map(step):
    return {row["nuclide"]: row for row in step["inventory"]}


def check_u235(result, lib, records, flux, plan, multiplier, awr):
    """G3a over the candidate's U-235 result."""
    steps = result["steps"]
    schedule = [(dt, multiplier * weight) for dt, weight in U235_SCHEDULE]
    out = {"multiplier": multiplier, "plan": {k: v for k, v in plan.items() if k != "products"},
           "plan_products": {f"{k[0]}_{k[1]}": v for k, v in sorted(plan["products"].items())},
           "tolerance": TOL_INDUCED, "xs_source": "streamed sig.npy rows (not `actinv inspect xs`)"}
    if len(steps) != len(schedule) or any("fission" not in s or "count" not in s["fission"] for s in steps):
        out["pass"] = False
        out["error"] = "the candidate result lacks fission.count on every step"
        return out
    network = u235_network(records, plan)
    forward = u235_integrate(network, schedule, N0_U235)
    # every other fissionable nuclide in the run inventory
    others = {}
    for step in steps:
        for row in step["inventory"]:
            key = (row["Z"] * 1000 + row["A"], row["LISO"])
            if row["Z"] >= HEAVY_METAL_MIN_Z and key not in NETWORK_STATES:
                others[key] = row["nuclide"]
    wanted, fission_rows = [], {}
    for key in others:
        for i in lib.rows_of(key):
            if lib.rows[i][1] == 18 and lib.rows[i][2] == 0:
                fission_rows.setdefault(key, []).append(i)
                wanted.append(i)
    sig = lib.stream(wanted)
    other_rate = {key: sum(fold(sig[i], flux) for i in rows) for key, rows in fission_rows.items()}
    out["other_fissionable_nuclides"] = {others[k]: {"rate_per_atom_per_s": r} for k, r in sorted(other_rate.items())}
    per_step, cumulative_u235, cumulative_other = [], 0.0, 0.0
    worst, worst_model, worst_arith = 0.0, 0.0, 0.0
    previous = {}
    for si, (step, (dt, mult), model) in enumerate(zip(steps, schedule, forward)):
        inv = inventory_map(step)
        current = {others[k]: inv.get(others[k], {}).get("atoms_per_g", 0.0) for k in other_rate}
        if mult > 0.0:
            cumulative_u235 += mult * sum(rate * integral for rate, integral in
                                          zip(plan["state_fission_rate_per_s"], model["integrals"]))
            for key, rate in other_rate.items():
                name = others[key]
                cumulative_other += mult * rate * 0.5 * (previous.get(name, 0.0) + current[name]) * dt
        previous = current
        expected = cumulative_u235 + cumulative_other
        count = step["fission"]["count"]
        induced = count["neutron_induced_fissions_per_g"]
        err = rel_err(induced, expected)
        worst = max(worst, err)
        run_u235 = inv.get("U235", {}).get("atoms_per_g")
        model_err = rel_err(run_u235, model["end"][0])
        worst_model = max(worst_model, model_err)
        spontaneous = count["spontaneous_fissions_per_g"]
        total = count["fissions_per_g"]
        arith = {"fissions_sum": rel_err(total, induced + spontaneous)}
        heavy_atoms = N0_U235
        arith["initial_heavy_metal_atoms"] = rel_err(count["initial_heavy_metal_atoms_per_g"], heavy_atoms)
        arith["burnup_percent"] = rel_err(count.get("burnup_percent"), 100.0 * total / heavy_atoms)
        mass_g = heavy_atoms * awr * NEUTRON_MASS_AMU / NA
        energy = count["energy_per_fission_MeV"]
        expected_mwd = total * energy * 1.0e6 * EV_J / J_PER_MWD / (mass_g / 1.0e3)
        arith["burnup_MWd_per_kg_hm"] = rel_err(count.get("burnup_MWd_per_kg_hm"), expected_mwd)
        arith["energy_per_fission_echo"] = rel_err(energy, ENERGY_PER_FISSION_MEV)
        worst_arith = max(worst_arith, *arith.values())
        per_step.append({
            "step": si + 1, "multiplier": mult, "dt_s": dt, "induced_run": induced, "induced_expected": expected,
            "induced_network_part": cumulative_u235, "induced_other_part": cumulative_other,
            "other_share": cumulative_other / expected if expected else None, "relative_error": err,
            "u235_atoms_run": run_u235, "u235_atoms_model": model["end"][0], "u235_model_relative_error": model_err,
            "isomer_atoms_model": model["end"][1], "pa235_atoms_model": model["end"][2],
            "isomer_atoms_run": inv.get("U235m1", {}).get("atoms_per_g"),
            "pa235_atoms_run": inv.get("Pa235", {}).get("atoms_per_g"),
            "spontaneous_fissions_run": spontaneous, "arithmetic_relative_errors": arith,
            "burnup_percent": count.get("burnup_percent"), "burnup_MWd_per_kg_hm": count.get("burnup_MWd_per_kg_hm")})
    out["steps"] = per_step
    out["worst_induced_relative_error"] = worst
    out["worst_model_relative_error"] = worst_model
    out["worst_arithmetic_relative_error"] = worst_arith
    out["tolerances"] = {"induced": TOL_INDUCED, "model_vs_inventory": TOL_MODEL_INVENTORY,
                         "arithmetic": TOL_ARITHMETIC}
    out["bound_note"] = ("terms neglected: U-236 (n,2n) back to U-235 and trapezoid error of the second-generation "
                         "fissioners; both scale with the optical depth squared or are below 1e-6 of the total at the "
                         f"sized optical depth {TARGET_OPTICAL_DEPTH}")
    out["pass"] = bool(worst <= TOL_INDUCED and worst_model <= TOL_MODEL_INVENTORY and worst_arith <= TOL_ARITHMETIC)
    return out


# ------------------------------------------------------------------------------ Cf-252 chain (G3b, G4)


def decay_chain(records, heads):
    """Reachable decay closure of the `heads` with each state's decay edges."""
    seen, stack, edges = set(heads), list(heads), {}
    while stack:
        key = stack.pop()
        edges[key] = decay_edges(records, key)
        for daughter in edges[key]["edges"]:
            if daughter not in seen:
                seen.add(daughter)
                stack.append(daughter)
    return edges


def chain_counts(edges, initial, step_seconds, digits=60):
    """Cumulative spontaneous-fission counts and populations at each step end from the initial populations
    {state: atoms}: exact exponential of the decay matrix augmented with a counter row (mpmath, `digits` digits)."""
    import mpmath as mp
    mp.mp.dps = digits
    nodes = sorted(edges)
    index = {key: i for i, key in enumerate(nodes)}
    n = len(nodes)
    matrix = mp.zeros(n + 1, n + 1)
    for key, info in edges.items():
        col = index[key]
        matrix[col, col] -= mp.mpf(info["lam"])
        for daughter, rate in info["edges"].items():
            matrix[index[daughter], col] += mp.mpf(rate)
        matrix[n, col] += mp.mpf(info["sf"])
    state = mp.zeros(n + 1, 1)
    for key, atoms in initial.items():
        state[index[key]] = mp.mpf(atoms)
    out = []
    for dt in step_seconds:
        state = mp.expm(matrix * mp.mpf(dt)) * state
        out.append({"sf_fissions": float(state[n]),
                    "atoms": {key: float(state[index[key]]) for key in nodes}})
    return out, nodes


CF252_STEPS = ["1800 s", "1 d", "30 d", "365 d"]
CF252_STEP_SECONDS = [1800.0, 86400.0, 30.0 * 86400.0, 365.0 * 86400.0]


def cf252_spec(base, decay, nubar_ref):
    spec = {
        "spec": "actinv-spec-1",
        "title": "P125 G3b/G4: decay-only Cf-252, spontaneous-fission count and neutron source",
        "projectile": base.get("projectile", "neutron"),
        "decay": {"primary": decay["primary"]["path"], "fallback": decay["fallback"]["path"]},
        "material": {"mass_g": 1.0, "basis": "atoms_per_g", "composition": {"Cf252": N0_CF252, "Cm250": N0_CM250}},
        "fission_yields": {"spontaneous": {"nubar_endf": {"path": nubar_ref["path"], "sha256": nubar_ref["sha256"]}}},
        "options": {"mode": "coupled", "prune": "none", "fission": {"energy_per_fission_MeV": ENERGY_PER_FISSION_MEV}},
        "schedule": [{"dt": dt, "flux": 0.0} for dt in CF252_STEPS],
    }
    return spec


def check_cf252_count(result, records):
    """G3b."""
    edges = decay_chain(records, [CF252, CM250])
    head = edges[CF252]
    exact, nodes = chain_counts(edges, {CF252: N0_CF252, CM250: N0_CM250}, CF252_STEP_SECONDS)
    out = {"chain_states": len(nodes), "head_lambda_per_s": head["lam"], "head_br_sf_effective": head["br_sf"],
           "tolerance": TOL_SF_COUNT, "solver": "mpmath 60 digits, exact matrix exponential with counter rows"}
    steps = result["steps"]
    if len(steps) != len(CF252_STEP_SECONDS) or any("fission" not in s or "count" not in s["fission"] for s in steps):
        out["pass"] = False
        out["error"] = "the candidate result lacks fission.count on every step"
        return out, exact, nodes
    per_step, worst = [], 0.0
    elapsed = 0.0
    closed_form_ok = None
    for si, (step, dt, ex) in enumerate(zip(steps, CF252_STEP_SECONDS, exact)):
        elapsed += dt
        count = step["fission"]["count"]
        run = count["spontaneous_fissions_per_g"]
        err = rel_err(run, ex["sf_fissions"])
        worst = max(worst, err)
        closed = N0_CF252 * (1.0 - math.exp(-head["lam"] * elapsed)) * head["br_sf"]
        closed_err = rel_err(run, closed)
        if si == 0:
            closed_form_ok = closed_err <= TOL_SF_CLOSED_FORM
        per_step.append({
            "step": si + 1, "t_s": elapsed, "t_s_run": step["t_s"], "sf_run": run, "sf_expected_chain": ex["sf_fissions"],
            "relative_error": err, "closed_form_Cf252_only": closed, "closed_form_relative_error": closed_err,
            "daughter_share_of_chain": 1.0 - closed / ex["sf_fissions"] if ex["sf_fissions"] else None,
            "induced_run": count["neutron_induced_fissions_per_g"],
            "fissions_sum_error": rel_err(count["fissions_per_g"], run + count["neutron_induced_fissions_per_g"]),
            "cf252_atoms_run": inventory_map(step).get("Cf252", {}).get("atoms_per_g"),
            "cf252_atoms_chain": ex["atoms"][CF252]})
    out["steps"] = per_step
    out["worst_relative_error"] = worst
    out["closed_form_first_step_ok"] = closed_form_ok
    out["closed_form_note"] = ("N0 (1 - exp(-lambda t)) BR_SF omits the daughters' fissions (Cm-248, ...); it is gated "
                               "on the first step, where they are below 1e-9, and tabulated afterwards")
    out["induced_is_zero"] = all(p["induced_run"] == 0.0 for p in per_step)
    out["pass"] = bool(worst <= TOL_SF_COUNT and closed_form_ok and out["induced_is_zero"])
    return out, exact, nodes


def check_cf252_source(result, records, nubar, nubar_endf_sha):
    """G4 over the Cf-252 decay-only result."""
    steps = result["steps"]
    cf = nubar.get(CF252)
    out = {"tolerance": TOL_NEUTRONS, "nubar_source": "own MF=1/MT=452 parse of the ENDF/B-VIII.0 decay file",
           "nubar_endf_sha256": nubar_endf_sha,
           "cf252_nubar": None if cf is None else {"value": cf[0], "basis": cf[1], "mt": cf[2]}}
    if cf is None or cf[0] != CF252_NUBAR_PROTOCOL or cf[2] != 452:
        out["pass"] = False
        out["error"] = f"Cf-252 nu-bar parsed as {cf}, the protocol states {CF252_NUBAR_PROTOCOL} from MT=452"
        return out
    if any("fission" not in s or "spontaneous_neutron_source" not in s["fission"] for s in steps):
        out["pass"] = False
        out["error"] = "the candidate result lacks fission.spontaneous_neutron_source on every step"
        return out
    edge_cache = {}
    per_step, worst = [], 0.0
    worst_uncovered = 0.0
    listed_ok = True
    for si, step in enumerate(steps):
        source = step["fission"]["spontaneous_neutron_source"]
        total, uncovered_rate = 0.0, 0.0
        by_nuclide, uncovered_states = {}, []
        for row in step["inventory"]:
            key = (row["Z"] * 1000 + row["A"], row["LISO"])
            if key not in records:
                continue
            if key not in edge_cache:
                edge_cache[key] = decay_edges(records, key)
            sf_rate = edge_cache[key]["sf"]
            if sf_rate <= 0.0:
                continue
            decays = sf_rate * row["atoms_per_g"]
            if key in nubar:
                by_nuclide[row["nuclide"]] = by_nuclide.get(row["nuclide"], 0.0) + decays * nubar[key][0]
                total += decays * nubar[key][0]
            else:
                uncovered_rate += decays
                uncovered_states.append(row["nuclide"])
        err_total = rel_err(source["neutrons_per_s_per_g"], total)
        cf_expected = by_nuclide.get("Cf252", 0.0)
        err_cf = rel_err(source["by_nuclide"].get("Cf252"), cf_expected)
        err_map = max([rel_err(source["by_nuclide"].get(k), v) for k, v in by_nuclide.items()] + [0.0])
        extra = sorted(set(source["by_nuclide"]) - set(by_nuclide))
        err_unc = rel_err(source["uncovered_sf_decays_per_s_per_g"], uncovered_rate)
        ledger = (result.get("ledger") or {}).get("spontaneous_fission") or {}
        ledger_uncovered = set(ledger.get("nubar_uncovered") or [])
        ledger_covered = set(ledger.get("nubar_covered") or [])
        missing_uncovered = sorted(set(uncovered_states) - ledger_uncovered)
        missing_covered = sorted(set(by_nuclide) - ledger_covered)
        if missing_uncovered or missing_covered or extra:
            listed_ok = False
        worst = max(worst, err_total, err_cf, err_map)
        worst_uncovered = max(worst_uncovered, err_unc)
        per_step.append({
            "step": si + 1, "neutrons_run": source["neutrons_per_s_per_g"], "neutrons_expected": total,
            "relative_error_total": err_total, "cf252_run": source["by_nuclide"].get("Cf252"),
            "cf252_expected": cf_expected, "relative_error_cf252": err_cf, "relative_error_by_nuclide_worst": err_map,
            "by_nuclide_expected": by_nuclide, "run_only_nuclides": extra,
            "uncovered_run": source["uncovered_sf_decays_per_s_per_g"], "uncovered_expected": uncovered_rate,
            "uncovered_relative_error": err_unc, "uncovered_states_expected": sorted(uncovered_states),
            "populated_sf_states_missing_from_ledger_uncovered": missing_uncovered,
            "covered_states_missing_from_ledger_covered": missing_covered})
    out["steps"] = per_step
    out["worst_relative_error"] = worst
    out["worst_uncovered_relative_error"] = worst_uncovered
    out["coverage_lists_consistent"] = listed_ok
    out["ledger_spontaneous_fission"] = {k: v for k, v in ((result.get("ledger") or {}).get("spontaneous_fission")
                                                          or {}).items()
                                         if k in ("nubar_covered", "nubar_uncovered", "uncovered_to_leakage",
                                                  "routed_to_products")}
    # the first-step magnitude is the sanity anchor: lambda BR N nu-bar for 1e21 atoms of Cf-252, per gram of material
    head = decay_edges(records, CF252)
    out["cf252_initial_neutrons_per_s_per_g"] = head["sf"] * N0_CF252 * cf[0]
    out["pass"] = bool(worst <= TOL_NEUTRONS and worst_uncovered <= TOL_NEUTRONS and listed_ok)
    return out


# ------------------------------------------------------------------------------ runs and gates


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def run_binary(binary, spec_path, out_path, log_path, cwd, timeout):
    with open(log_path, "wb") as log:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=str(cwd), stdout=log,
                              stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode


def gate_g0():
    digest = sha256_file(PROTOCOL)
    name = f"protocols/{PROTOCOL.name}"
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    hit = [line for line in lines if line.startswith(digest) and line.split()[-1] == name]
    out = {"protocol": name, "sha256": digest, "in_protocol_hash_txt": bool(hit)}
    try:
        log = subprocess.run(["git", "log", "--format=%H", f"-S{digest}", "--", "protocols/protocol_hash.txt"],
                             cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        out["first_commit_listing_the_hash"] = log.stdout.split()[-1] if log.stdout.split() else None
    except (OSError, subprocess.TimeoutExpired) as error:
        out["git_error"] = f"{type(error).__name__}: {error}"
    out["pass"] = bool(hit)
    return out


def gate_g2(pairs):
    out = {"cases": {}}
    ok = True
    for label, (ref, cand) in pairs.items():
        if ref is None or cand is None:
            out["cases"][label] = {"pass": False, "error": "a run failed; see runs"}
            ok = False
            continue
        ref_s, ref_dropped = strip_timing(ref)
        cand_s, cand_dropped = strip_timing(cand)
        equal = canonical(ref_s) == canonical(cand_s)
        case = {"canonical_equal_without_timing": equal, "timing_keys_dropped": sorted(set(ref_dropped + cand_dropped)),
                "steps": len(ref.get("steps", [])), "pass": equal}
        if not equal:
            case["first_differences"] = first_differences(ref_s, cand_s)
        out["cases"][label] = case
        ok = ok and equal
    out["timing_keys_policy"] = {"top_level": ["ms"], "anywhere": ["wall_time_s", "cells_per_s"]}
    out["pass"] = ok
    return out


def search_sfy():
    """Look for an ENDF/B spontaneous-fission yield sublibrary under ~/nuclear-data."""
    roots = [Path("~/nuclear-data").expanduser()]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("*sfy*", "*SFY*", "sfy-*", "*spontaneous*yield*"):
            for path in root.rglob(pattern):
                if path.is_file() and len(found) < MAX_LISTED:
                    found.append(str(path))
    return sorted(set(found))


def gate_g5():
    found = search_sfy()
    if not found:
        return {"status": "data_limited", "pass": None, "blocking": False,
                "reason": "no ENDF/B spontaneous-fission yield (sfy) sublibrary is installed under ~/nuclear-data; "
                          "routing is covered by the synthetic MT=454 unit tests",
                "searched": "~/nuclear-data, patterns *sfy*, *spontaneous*yield*", "found": []}
    return {"status": "files_present_check_not_automated", "pass": None, "blocking": False, "found": found,
            "reason": "an sfy file exists; the Cf-252 product-inventory check (lambda BR N Y_i integrated) needs the "
                      "fragment chains solved and is not implemented here"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="binary built from the P125 base commit")
    ap.add_argument("--candidate", required=True, help="binary built from the P125 implementation commit")
    ap.add_argument("--workdir", default="target/p125-work")
    ap.add_argument("--out", default="results/p125_fission.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    ap.add_argument("--data-dir", default="actinv-data/v1.1.0", help="installed actinv-data directory")
    ap.add_argument("--nfpy-u235", default="~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf")
    args = ap.parse_args(argv)
    args.reference = str(Path(args.reference).resolve())
    args.candidate = str(Path(args.candidate).resolve())

    def resolve(path):
        path = Path(path).expanduser()
        return path if path.is_absolute() else ROOT / path

    data_dir = resolve(args.data_dir)
    workdir = resolve(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = resolve(args.out)
    yields_path = resolve(args.nfpy_u235)

    library = record_file(data_dir / "activation" / LIBRARY_NAME)
    library_index = record_file(data_dir / "activation" / LIBRARY_NAME.replace(".npz", "_index.json"))
    decay = {"primary": record_file(data_dir / "decay" / DECAY_PRIMARY_NAME),
             "fallback": record_file(data_dir / "decay" / DECAY_FALLBACK_NAME)}
    yields = record_file(yields_path)
    record = {"protocol": "ACTINV-P125", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "reference_binary": record_file(args.reference),
              "candidate_binary": record_file(args.candidate), "fns_spec": record_file(FNS_SPEC),
              "inputs": {"library": library, "library_index": library_index, "decay": decay, "u235_yields": yields},
              "runs": {}, "gates": {},
              "external_gates": {"G1": "fmt, clippy -D warnings, workspace tests: run by the coordinator",
                                 "G6": "CI on the pushed commit and the committed baselines: run by the coordinator"}}

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

    # ---- G0
    record["gates"]["G0"] = gate_g0()

    # ---- independent inputs: decay records, library, spectrum
    base = json.loads(FNS_SPEC.read_text())
    absolutize_paths(base)
    flux = ascending_flux(base["spectrum"])
    records, nubar, decay_info = merge_decay(decay["primary"]["path"], decay["fallback"]["path"])
    record["decay_parse"] = decay_info
    lib = Library(library["path"], library_index["path"])
    plan = u235_plan(lib, records, flux)
    multiplier = sizing(plan, U235_SIZING_DAYS)
    record["u235_sizing"] = {"removal_rate_per_s": plan["removal_rate_per_s"], "schedule_days_weights": U235_SIZING_DAYS,
                             "target_optical_depth": TARGET_OPTICAL_DEPTH, "multiplier": multiplier}

    # ---- specs
    spec_count = u235_spec(base, library, decay, yields, multiplier, True)
    spec_plain = u235_spec(base, library, decay, yields, multiplier, False)
    spec_plain["title"] = "P125 G2: U-235 irradiation with fission yields (no P125 option)"
    nubar_ref = decay["primary"]
    spec_cf = cf252_spec(base, decay, nubar_ref)
    paths = {}
    for label, spec in (("u235_count", spec_count), ("u235_plain", spec_plain), ("cf252_decay_only", spec_cf)):
        paths[label] = workdir / f"{label}.spec.json"
        paths[label].write_text(json.dumps(spec, indent=1) + "\n")

    # ---- G2
    pairs = {}
    pairs["fns_fe_5min"] = (execute("g2_fns_reference", args.reference, FNS_SPEC, ROOT),
                            execute("g2_fns_candidate", args.candidate, FNS_SPEC, ROOT))
    pairs["u235_yields"] = (execute("g2_u235_reference", args.reference, paths["u235_plain"], workdir),
                            execute("g2_u235_candidate", args.candidate, paths["u235_plain"], workdir))
    record["gates"]["G2"] = gate_g2(pairs)

    # ---- G3a
    cand_u235 = execute("g3a_u235_candidate", args.candidate, paths["u235_count"], workdir)
    if cand_u235 is not None:
        try:
            record["gates"]["G3a"] = check_u235(cand_u235, lib, records, flux, plan, multiplier,
                                                records[U235]["awr"])
        except (KeyError, ValueError, IndexError, ImportError, ZeroDivisionError) as error:
            record["gates"]["G3a"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    else:
        record["gates"]["G3a"] = {"pass": False, "error": "the candidate run failed; see runs"}

    # ---- G3b and G4
    cand_cf = execute("g3b_cf252_candidate", args.candidate, paths["cf252_decay_only"], workdir)
    if cand_cf is not None:
        try:
            g3b, _exact, _nodes = check_cf252_count(cand_cf, records)
            record["gates"]["G3b"] = g3b
        except (KeyError, ValueError, IndexError, ImportError, ZeroDivisionError) as error:
            record["gates"]["G3b"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        try:
            record["gates"]["G4"] = check_cf252_source(cand_cf, records, nubar, nubar_ref["sha256"])
        except (KeyError, ValueError, IndexError, ZeroDivisionError) as error:
            record["gates"]["G4"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    else:
        reason = ("the candidate decay-only run failed; see runs (a decay-only spec needs the P124 integration, so "
                  "this is the dependency noted in the protocol if the build lacks it)")
        record["gates"]["G3b"] = {"pass": False, "error": reason}
        record["gates"]["G4"] = {"pass": False, "error": reason}
    g3 = {"pass": bool(record["gates"]["G3a"].get("pass") and record["gates"]["G3b"].get("pass")),
          "parts": ["G3a", "G3b"]}
    record["gates"]["G3"] = g3

    # ---- G5
    record["gates"]["G5"] = gate_g5()

    blocking = ("G0", "G2", "G3", "G4")
    overall = all(bool(record["gates"][g].get("pass")) for g in blocking)
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in ("G0", "G2", "G3a", "G3b", "G3", "G4"):
        print(f"P125 {gate}: {'PASS' if record['gates'][gate].get('pass') else 'FAIL'}")
    print(f"P125 G5: {record['gates']['G5']['status']}")
    g3a = record["gates"]["G3a"]
    if "worst_induced_relative_error" in g3a:
        print(f"  G3a: worst induced error {g3a['worst_induced_relative_error']:.3e} (tol {TOL_INDUCED:g}), "
              f"model {g3a['worst_model_relative_error']:.3e}, arithmetic {g3a['worst_arithmetic_relative_error']:.3e}")
    g3b = record["gates"]["G3b"]
    if "worst_relative_error" in g3b:
        print(f"  G3b: worst SF-count error {g3b['worst_relative_error']:.3e} (tol {TOL_SF_COUNT:g})")
    g4 = record["gates"]["G4"]
    if "worst_relative_error" in g4:
        print(f"  G4: worst neutron-source error {g4['worst_relative_error']:.3e} (tol {TOL_NEUTRONS:g})")
    print(f"P125 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
