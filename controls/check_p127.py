#!/usr/bin/env python3
"""P127 gates G2-G6 (protocols/ACTINV-P127_PROTOCOL.md): full pathways and routes analysis.

  G2  The unmodified gate spec (examples/fns_fe_5min.json) run by the reference and the candidate binary gives
      byte-identical result files once the wall-clock `"ms": <number>` is masked.
  G3  Independent route recomputation. The pathways spec (targets Mn56, Mn54, Cr51, Fe55, path_floor 1e-6, by_step)
      is run by the candidate; every reported route is integrated again here as a linear chain through every
      schedule step with high-precision matrix exponentials (mpmath) and compared, final value and every
      `atoms_per_g_by_step` entry, at 1e-8 relative.
  G4  Closure per target: sum of the listed routes + residual = total (1e-12 relative); total = the target's atoms in
      the result's final inventory (1e-12); explained fraction >= 0.999 for every target holding at least 1e-6 of the
      final inventory's atoms.
  G5  The same spec forced to the other solver mode (trace <-> coupled): routes present in both agree at 1e-8
      relative or within the coupled run's numerical floor (the rule controls/check_p122.py applies).
  G6  Report only: the pathway section of the FISPACT-II output of the FNS Fe 5-minute case, tabulated against the
      ACTINV routes. No verdict (the libraries differ).

Overall PASS iff G2, G3, G4 and G5 pass.

What G3 rebuilds, and the conventions it has to infer. The result carries no per-reaction rates or route-edge
rates, so every rate is rederived from the inputs, never read from a result:
  * decay constants and branches from this script's own ENDF-6 MF=8 MT=457 parse of the spec's decay files
    (primary first, fallback only for keys the primary lacks); lambda = ln2 / T1/2, zero for stable states;
    branches scaled/leaked exactly as crates/actinv-core/src/chain.rs does (a positive-branch sum further than 1e-5
    from 1 is renormalised when above 1 and leaks the shortfall when below; within 1e-5 it is used as given);
    daughters from the RTYP digit chain, RFS isomer with the ground-state fallback, a missing or self daughter and
    spontaneous fission leak.
  * reaction rates from the library NPZ: sig rows streamed by target, collapsed with the spec spectrum
    (reversed when `descending`, scaled to `total`) as sum(sig_g phi_g) / sum(phi_g) * 1e-24 * sum(phi_g) per
    target atom, times the step's `flux` multiplier. Product rows go to (ZAP, LFS), else (ZAP, 0), else leak; an
    LMF=-2 row leaks.
  * a node's FULL removal rate is lambda + multiplier * (sum of its ZAP=-1 loss rows). That is the engine's
    diagonal convention. The sum of the node's outgoing product rows is computed too and reported as a diagnostic
    (conservation), not used.
  * the edge rate between two consecutive route nodes is the sum of all decay branches and reaction rows that
    take the first to the second (a route carries one edge per node pair, whatever its label).
  * trace mode: the first node of a route is the constant reservoir (diagonal 0). The engine integrates it as
    1 atom with edge rate (bulk atoms x per-atom rate); here the reservoir starts with its bulk atoms and the edge
    carries the per-atom rate. The product is identical and the matrix is better scaled. Bulk atoms per gram come
    from this script's own parse of crates/actinv-data/src/tables.rs (natural abundance and atomic mass) and the
    spec's wt_percent composition, atoms = N_A * (w/100 / M) * abundance. Coupled mode: the first node is the
    initial nuclide with the same atoms and its full removal rate on the diagonal.
  * later nodes are tracked states with their full removal rate; a repeated nuclide in a loop route is an
    independent chain position, as in the engine.
  * the engine clamps round-off negatives to zero; mpmath does not need it.
Resolution rule: a value passes at 1e-8 relative, or (counted separately as `resolution_only`) when its absolute
difference is below 1e-10 of the largest population the route's own chain reaches (docs/guide/results.md: the dense
integration works to the absolute precision of its largest term).

Usage:
  check_p127.py --reference REF_BIN --candidate CAND_BIN
      [--fispact-out ~/nuclear-data/conderc-fns/fns/Fe/TENDL-2017_1996exp_5min.out]
      [--workdir target/p127-work] [--out results/p127_pathways.json] [--timeout 3600] [--reuse]
  check_p127.py --self-check      (offline unit checks of the independent integrator and parsers; runs no binary)

Run inside the required systemd cgroup. Runs are serial, without a shell, each under a timeout. Exit 0 on PASS,
1 on FAIL.
"""
import argparse
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import mpmath
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
GATE_SPEC_SHA256 = "ed55ed00f6c00fc4a2351e18332e98e7feb33d9b7ca65e7ba861e0e5e8feef39"
PROTOCOL = ROOT / "protocols" / "ACTINV-P127_PROTOCOL.md"
TABLES_RS = ROOT / "crates" / "actinv-data" / "src" / "tables.rs"

NA = 6.02214076e23  # crates/actinv-data/src/composition.rs, NA
BARN_CM2 = 1.0e-24  # crates/actinv-core/src/quantity.rs, BARN_IN_SQUARE_CENTIMETRES
BRANCHING_TOLERANCE = 1e-5  # crates/actinv-core/src/chain.rs, BRANCHING_TOLERANCE

TARGETS = ["Mn56", "Mn54", "Cr51", "Fe55"]
PATH_FLOOR = 1e-6
ROUTE_REL_TOL = 1e-8
RESOLUTION_FRACTION = 1e-10
CLOSURE_TOL = 1e-12
EXPLAINED_MIN = 0.999
SIGNIFICANT_TARGET_SHARE = 1e-6
UNMATCHED_ROUTE_SLACK = 2.0  # G5: a route in one mode only must lie within this multiple of path_floor
TRACE_COUPLED_REL_TOL = 1e-8  # protocols/ACTINV-P5_PROTOCOL.md, G7
MP_DPS = 40
MAX_LISTED = 20
FIRST_DIFFERENCES = 8

mpmath.mp.dps = MP_DPS

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
SYMBOL_Z = {s: z for z, s in enumerate(SYMBOLS, start=1)}
NAME_RE = re.compile(r"^([A-Z][a-z]?)(\d+)(?:m(\d+))?$")

# RTYP digit -> (dZ, dA); 3 is an isomeric transition, 6 (spontaneous fission) and unknown digits have no daughter.
DECAY_STEP = {1: (1, 0), 2: (-1, 0), 3: (0, 0), 4: (-2, -4), 5: (0, -1), 7: (-1, -1)}
DURATION_UNITS = {"": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
                  "m": 60.0, "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
                  "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
                  "d": 86400.0, "day": 86400.0, "days": 86400.0,
                  "y": 365.25 * 86400.0, "yr": 365.25 * 86400.0, "yrs": 365.25 * 86400.0,
                  "year": 365.25 * 86400.0, "years": 365.25 * 86400.0}


class GateError(Exception):
    """A gate could not be evaluated (as opposed to evaluating to a mismatch)."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


# ------------------------------------------------------------------------------ names and specs


def key_of(name):
    """(ZA, LISO) of a canonical nuclide name such as `Mn56` or `Mn58m1`."""
    match = NAME_RE.match(name)
    if not match or match.group(1) not in SYMBOL_Z:
        raise GateError(f"cannot parse nuclide name {name!r}")
    liso = int(match.group(3)) if match.group(3) else 0
    return SYMBOL_Z[match.group(1)] * 1000 + int(match.group(2)), liso


def name_of(key):
    za, liso = key
    base = f"{SYMBOLS[za // 1000 - 1]}{za % 1000}"
    return f"{base}m{liso}" if liso > 0 else base


def parse_duration(text):
    match = re.match(r"^\s*([-+0-9.eE]+?)\s*([A-Za-z]*)\s*$", text)
    if not match:
        raise GateError(f"bad duration {text!r}")
    unit = match.group(2).lower()
    if unit not in DURATION_UNITS:
        raise GateError(f"unsupported duration unit in {text!r}")
    return float(match.group(1)) * DURATION_UNITS[unit]


def ascending_flux(spectrum):
    """The solver's group flux in ascending energy, scaled to `total` when given (Spectrum::ascending_flux)."""
    f = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending", False):
        f.reverse()
    total = spectrum.get("total")
    if total is not None:
        s = math.fsum(f)
        if s > 0.0:
            k = total / s
            f = [v * k for v in f]
    return np.array(f, dtype="<f8")


def schedule_of(spec, allow_overrides=False):
    """[(duration s, flux multiplier)] per step. Per-step feed/removal/library/spectrum are refused unless
    `allow_overrides` (the library and spectrum are then handled by the caller)."""
    out = []
    for step in spec["schedule"]:
        if step.get("feed") or step.get("removal"):
            raise GateError("this checker does not handle feed or removal steps")
        if not allow_overrides and (step.get("library") or step.get("spectrum")):
            raise GateError("this checker handles schedules of plain (dt, flux) steps only")
        out.append((parse_duration(step["dt"]), float(step["flux"])))
    return out


def load_isotopes(path=TABLES_RS):
    """Natural-abundance rows (symbol, A, LISO, abundance, atomic mass) from the engine's embedded table."""
    pattern = re.compile(r'\("([A-Z][a-z]?)",\s*(\d+),\s*(\d+),\s*([0-9.eE+-]+),\s*([0-9.eE+-]+)\)')
    rows = {}
    for symbol, a, liso, abundance, mass in pattern.findall(Path(path).read_text()):
        rows.setdefault(symbol, []).append((int(a), int(liso), float(abundance), float(mass)))
    if not rows:
        raise GateError(f"no isotope rows found in {path}")
    return rows


def bulk_atoms_per_g(spec, isotopes):
    """Initial atoms per gram of material by (ZA, LISO) for a wt_percent element composition (composition.rs)."""
    material = spec["material"]
    if material.get("basis") != "wt_percent":
        raise GateError(f"material basis {material.get('basis')!r} is not handled (wt_percent only)")
    out = {}
    for element, value in material["composition"].items():
        symbol = element[:1].upper() + element[1:].lower()
        if symbol not in isotopes:
            raise GateError(f"composition entry {element!r} is not a natural element in the isotope table")
        rows = isotopes[symbol]
        molar = sum(ab * mass for _a, _l, ab, mass in rows)
        atoms = NA * ((value / 100.0) / molar)
        z = SYMBOL_Z[symbol]
        for a, liso, ab, _mass in rows:
            out[(z * 1000 + a, liso)] = out.get((z * 1000 + a, liso), 0.0) + atoms * ab
    return out


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
    """One MF=8/MT=457 section: half-life, NST and the decay-mode list (RTYP, RFS, BR)."""
    za, awr, _lis, liso, nst, _nsp = cont(lines[0])
    if not (math.isfinite(za) and za > 0):
        raise ValueError(f"invalid decay HEAD ZA {za}")
    energy_head, _energies, nxt = read_list(lines, 1)
    mode_head, mode_values, _ = read_list(lines, nxt)
    ndk = mode_head[5]
    if ndk * 6 > len(mode_values):
        raise ValueError(f"decay mode LIST has {len(mode_values)} fields for {ndk} modes")
    modes = [(mode_values[6 * k], mode_values[6 * k + 1], mode_values[6 * k + 4]) for k in range(ndk)]
    return {"za": int(round(za)), "liso": liso, "nst": nst, "half_life": energy_head[0], "modes": modes}


def scan_decay_file(path, needed):
    """Stream an ENDF-6 decay sublibrary. Returns (records for the `needed` (ZA, LISO) keys, set of every key).
    Sections are delimited the way the engine delimits them: consecutive lines tagged MF=8 MT=457 under one MAT."""
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
    """Merge as the engine does: the primary wins; the fallback supplies only keys absent from the primary.
    Returns (records for `needed`, set of every chain key, info)."""
    p_records, p_keys = scan_decay_file(primary, needed)
    records = dict(p_records)
    chain_keys = set(p_keys)
    info = {"primary_keys": len(p_keys), "from_primary": len(p_records), "from_fallback": 0}
    if fallback:
        f_records, f_keys = scan_decay_file(fallback, needed - set(p_records) - p_keys)
        for key, rec in f_records.items():
            records.setdefault(key, rec)
        chain_keys |= f_keys
        info["fallback_keys"] = len(f_keys)
        info["from_fallback"] = len(f_records)
    info["chain_keys"] = len(chain_keys)
    info["needed_without_record"] = sorted(name_of(k) for k in needed - set(records))[:MAX_LISTED]
    return records, chain_keys, info


def lam_of(rec):
    if rec is None or rec["nst"] == 1 or rec["half_life"] <= 0.0:
        return 0.0
    return math.log(2.0) / rec["half_life"]


def rtyp_digits(rtyp):
    text = f"{rtyp:.6f}".rstrip("0").rstrip(".")
    return [int(c) for c in text if c.isdigit()]


def round_half_away(x):
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


# ------------------------------------------------------------------------------ the activation library


class Library:
    """A groupwise activation NPZ: `rows` (target, MT, ZAP, LFS, LMF), `sig` (rows x groups, barns, ascending
    energy) and `bounds`, with the index JSON beside it. `sig` is streamed row by row, never held whole."""

    def __init__(self, npz_path):
        self.path = Path(npz_path)
        text = str(self.path)
        index_path = text[:-4] + "_index.json" if text.endswith(".npz") else text + "_index.json"
        self.index_path = Path(index_path)
        self.index = json.loads(self.index_path.read_text())
        self.targets = [(int(t["za"]), int(t["liso"])) for t in self.index["targets"]]
        if len(set(self.targets)) != len(self.targets):
            raise GateError(f"{index_path}: duplicate targets")
        self.target_index = {key: i for i, key in enumerate(self.targets)}
        with np.load(self.path) as z:  # lazy: only `rows` and `bounds` are decoded here
            self.rows = np.array(z["rows"])
            self.bounds = np.array(z["bounds"])
        self.ngroups = len(self.bounds) - 1
        if self.rows.ndim != 2 or self.rows.shape[1] != 5:
            raise GateError(f"{self.path}: rows has shape {self.rows.shape}")
        if not np.all(np.diff(self.bounds) > 0.0):
            raise GateError("library bounds are not strictly ascending")
        self._rates = {}  # (phi key, row) -> rate per target atom, filled by `collapse`

    def row_indices(self, key):
        ti = self.target_index.get(key)
        return [] if ti is None else np.nonzero(self.rows[:, 0] == ti)[0].tolist()

    def _stream(self, wanted):
        """sig[row, :] for the sorted row numbers `wanted`, yielded in order."""
        with zipfile.ZipFile(self.path) as zf:
            with zf.open("sig.npy") as fp:
                version = np.lib.format.read_magic(fp)
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, fortran, dtype = reader(fp)
                if fortran or dtype != np.dtype("<f8") or shape != (self.rows.shape[0], self.ngroups):
                    raise GateError(f"sig.npy is {shape} {dtype} fortran={fortran}; expected "
                                    f"({self.rows.shape[0]}, {self.ngroups}) <f8 C order")
                row_bytes = self.ngroups * 8
                base = fp.tell()
                k = 0
                while k < len(wanted):
                    run = 1
                    while k + run < len(wanted) and wanted[k + run] == wanted[k] + run:
                        run += 1
                    fp.seek(base + int(wanted[k]) * row_bytes)
                    chunks, left = [], run * row_bytes
                    while left > 0:
                        part = fp.read(left)
                        if not part:
                            raise GateError("sig.npy ended early")
                        chunks.append(part)
                        left -= len(part)
                    block = np.frombuffer(b"".join(chunks), dtype="<f8").reshape(run, self.ngroups)
                    for i in range(run):
                        yield int(wanted[k + i]), block[i]
                    k += run

    def collapse(self, keys, phi):
        """Per-target-atom reaction rate (s^-1) of every row of the targets `keys` under the group flux `phi`:
        sum(sig_g phi_g) / sum(phi_g) * 1e-24 * sum(phi_g). Returns {key: [(mt, zap, lfs, lmf, rate)]}."""
        phi_key = hashlib.sha256(phi.tobytes()).hexdigest()
        if len(phi) != self.ngroups:
            raise GateError(f"spectrum has {len(phi)} groups, library has {self.ngroups}")
        wanted = sorted({r for key in keys for r in self.row_indices(key)})
        missing = [r for r in wanted if (phi_key, r) not in self._rates]
        flux_sum = math.fsum(phi.tolist())
        for r, sig_row in self._stream(missing):
            xs = math.fsum((sig_row * phi).tolist()) / flux_sum if flux_sum > 0.0 else 0.0
            self._rates[(phi_key, r)] = xs * (BARN_CM2 * flux_sum)
        out = {}
        for key in keys:
            out[key] = [(int(self.rows[r, 1]), int(self.rows[r, 2]), int(self.rows[r, 3]), int(self.rows[r, 4]),
                         self._rates[(phi_key, r)]) for r in self.row_indices(key)]
        return out


class Network:
    """Per-node decay and reaction rates assembled the way chain.rs does, from an independent parse."""

    def __init__(self, records, chain_keys, library, phi):
        self.records = records
        self.chain_keys = chain_keys
        self.library = library
        self.phi = phi
        self._cache = {}
        self._rows = {}

    def prepare(self, keys):
        todo = [k for k in keys if k not in self._rows]
        self._rows.update(self.library.collapse(todo, self.phi))

    def _daughter(self, want):
        if want in self.chain_keys:
            return want
        ground = (want[0], 0)
        return ground if ground in self.chain_keys else None

    def node(self, key):
        """{lambda, decay: {to: rate}, decay_leak, loss, react: {to: rate}, react_leak, fission} for one nuclide.
        `react` and `loss` are per atom at multiplier 1; decay does not scale with the flux multiplier."""
        if key in self._cache:
            return self._cache[key]
        rec = self.records.get(key)
        lam = lam_of(rec)
        decay, decay_leak = {}, 0.0
        if rec is not None and lam > 0.0:
            assigned = 0.0
            for _rtyp, _rfs, br in rec["modes"]:
                if br > 0.0:
                    assigned += br
            scale = 1.0
            if abs(assigned - 1.0) > BRANCHING_TOLERANCE:
                if assigned < 1.0:
                    decay_leak += lam * (1.0 - assigned)
                else:
                    scale = 1.0 / assigned
            z, a = key[0] // 1000, key[0] % 1000
            for rtyp, rfs, br in rec["modes"]:
                if br <= 0.0:
                    continue
                rate = lam * br * scale
                zz, aa, bad = z, a, False
                for digit in rtyp_digits(rtyp):
                    if digit == 6 or digit not in DECAY_STEP:
                        bad = True
                        break
                    dz, da = DECAY_STEP[digit]
                    zz, aa = zz + dz, aa + da
                if bad:
                    decay_leak += rate
                    continue
                to = self._daughter((zz * 1000 + aa, int(round_half_away(rfs))))
                if to is None or to == key:
                    decay_leak += rate
                else:
                    decay[to] = decay.get(to, 0.0) + rate
        loss, react, react_leak, fission = 0.0, {}, 0.0, 0.0
        for mt, zap, lfs, lmf, rate in self._rows.get(key, []):
            if zap == -1:
                loss += rate
                continue
            if rate == 0.0:
                continue
            if mt == 18 and zap == 0:
                fission += rate
                continue
            if lmf == -2:
                react_leak += rate
                continue
            to = self._daughter((zap, lfs))
            if to is None:
                react_leak += rate
            else:
                react[to] = react.get(to, 0.0) + rate
        out = {"lambda": lam, "decay": decay, "decay_leak": decay_leak, "loss": loss, "react": react,
               "react_leak": react_leak, "fission": fission}
        self._cache[key] = out
        return out

    def removal(self, key, multiplier):
        n = self.node(key)
        return n["lambda"] + multiplier * n["loss"]

    def edge(self, src, dst, multiplier):
        n = self.node(src)
        return n["decay"].get(dst, 0.0) + multiplier * n["react"].get(dst, 0.0)

    def conservation(self, key):
        """Relative gap between the loss rows and the sum of the outgoing product rows (diagnostic)."""
        n = self.node(key)
        out = sum(n["react"].values()) + n["react_leak"] + n["fission"]
        return None if n["loss"] == 0.0 and out == 0.0 else rel_diff(n["loss"], out)


# ------------------------------------------------------------------------------ independent chain integration


def integrate_chain(diag, sub, dts, init):
    """Atoms at every node of the chain x0 -> x1 -> ... -> xk after each step, from `init` atoms at x0.
    `diag[s][i]` is node i's removal rate in step s (>= 0), `sub[s][i]` the rate of the edge i -> i+1.
    Dense mpmath exponential of the (k+1)x(k+1) lower-bidiagonal matrix per step. Returns (per-step last-node
    values as floats, largest population reached anywhere in the chain)."""
    k = len(diag[0])
    v = mpmath.matrix([mpmath.mpf(init)] + [mpmath.mpf(0)] * (k - 1))
    peak = mpmath.mpf(init)
    out = []
    for s, dt in enumerate(dts):
        h = mpmath.mpf(dt)
        m = mpmath.zeros(k, k)
        for i in range(k):
            m[i, i] = -mpmath.mpf(diag[s][i]) * h
        for i in range(k - 1):
            m[i + 1, i] = mpmath.mpf(sub[s][i]) * h
        v = mpmath.expm(m) * v
        for i in range(k):
            peak = max(peak, v[i])
        out.append(float(v[k - 1]))
    return out, float(peak)


def compare_value(got, want, scale):
    """('strict' | 'resolution_only' | 'fail', relative difference)."""
    diff = abs(got - want)
    rel = diff / abs(want) if want != 0.0 else (0.0 if got == 0.0 else math.inf)
    if rel <= ROUTE_REL_TOL:
        return "strict", rel
    if diff <= RESOLUTION_FRACTION * scale:
        return "resolution_only", rel
    return "fail", rel


# ------------------------------------------------------------------------------ result files and runs


def mask_ms(raw):
    """Result bytes with the wall-clock `"ms": <number>` replaced by a constant; returns (bytes, occurrences)."""
    return re.subn(rb'("ms":\s*)[-+0-9.eE]+', rb"\g<1>0", raw)


def first_byte_difference(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            lo = max(0, i - 60)
            return {"offset": i, "reference": a[lo:i + 60].decode("utf-8", "replace"),
                    "candidate": b[lo:i + 60].decode("utf-8", "replace")}
    return {"offset": n, "note": f"lengths differ: {len(a)} vs {len(b)}"} if len(a) != len(b) else None


def run_binary(binary, spec_path, out_path, log_path, cwd, timeout):
    with open(log_path, "wb") as log:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=str(cwd), stdout=log,
                              stderr=subprocess.STDOUT, timeout=timeout)
    return proc.returncode


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


def pathways_spec(base_spec, mode, title_suffix):
    spec = copy.deepcopy(base_spec)
    absolutize_paths(spec)
    spec["pathways"] = {"targets": list(TARGETS), "path_floor": PATH_FLOOR, "by_step": True}
    if mode is not None:
        spec.setdefault("options", {})["mode"] = mode
    spec["title"] = f"{spec.get('title', 'P127')} [{title_suffix}]"
    return spec


def decay_files(spec):
    decay = spec.get("decay", {})
    extra = sorted(set(decay) - {"primary", "fallback"})
    if extra:
        raise GateError(f"decay section carries {extra}; this checker parses only primary and fallback")
    return decay.get("primary"), decay.get("fallback") or None


# ------------------------------------------------------------------------------ gates


def data_inputs(spec):
    """SHA-256 of the data files a spec reads: library NPZ and index, decay primary and fallback."""
    library = Path(spec["library"]["path"])
    index = Path(str(library)[:-4] + "_index.json" if str(library).endswith(".npz") else str(library) + "_index.json")
    primary, fallback = decay_files(spec)
    out = {"library": record_file(library), "library_index": record_file(index), "decay_primary": record_file(primary)}
    if fallback:
        out["decay_fallback"] = record_file(fallback)
    return out


def gate_g2(ref_path, cand_path, spec_sha_ok):
    ref_raw, cand_raw = Path(ref_path).read_bytes(), Path(cand_path).read_bytes()
    ref_masked, n_ref = mask_ms(ref_raw)
    cand_masked, n_cand = mask_ms(cand_raw)
    equal = ref_masked == cand_masked
    out = {"bytes_reference": len(ref_raw), "bytes_candidate": len(cand_raw), "ms_masked_reference": n_ref,
           "ms_masked_candidate": n_cand, "byte_identical_after_ms_mask": equal,
           "spec_sha256_matches_protocol": spec_sha_ok,
           "no_pathway_routes_key_in_either": b'"pathway_routes"' not in ref_raw + cand_raw}
    if not equal:
        out["first_difference"] = first_byte_difference(ref_masked, cand_masked)
    out["pass"] = bool(equal and spec_sha_ok and n_ref == 1 and n_cand == 1 and out["no_pathway_routes_key_in_either"])
    return out


def route_inputs(result, spec):
    """Everything G3 needs beyond the result: rates for every node on every reported route."""
    routes = result.get("pathway_routes")
    if not isinstance(routes, dict):
        raise GateError("the result has no pathway_routes object")
    mode = result.get("mode")
    if mode not in ("trace", "coupled"):
        raise GateError(f"result mode {mode!r} is not trace or coupled")
    names = set()
    for target in routes["targets"]:
        for path in target["paths"]:
            names.update(path["nuclides"])
    keys = {key_of(n) for n in names}
    primary, fallback = decay_files(spec)
    records, chain_keys, decay_info = load_decay(primary, fallback, keys)
    library = Library(spec["library"]["path"])
    phi = ascending_flux(spec["spectrum"])
    net = Network(records, chain_keys, library, phi)
    net.prepare(sorted(keys))
    return routes, mode, keys, net, decay_info, library, phi


def gate_g3(result, spec):
    routes, mode, keys, net, decay_info, library, phi = route_inputs(result, spec)
    schedule = schedule_of(spec)
    isotopes = load_isotopes()
    bulk = bulk_atoms_per_g(spec, isotopes)
    dts = [dt for dt, _fl in schedule]
    mults = [fl for _dt, fl in schedule]
    steps_out = result["steps"]
    out = {"mode": mode, "library": str(library.path), "library_rows": int(library.rows.shape[0]),
           "library_groups": library.ngroups, "decay": decay_info, "mpmath_dps": MP_DPS,
           "spectrum_total_flux": math.fsum(phi.tolist()), "schedule_steps": len(schedule),
           "bulk_atoms_per_g": {name_of(k): v for k, v in sorted(bulk.items())},
           "tolerance": {"relative": ROUTE_REL_TOL, "resolution_fraction_of_chain_peak": RESOLUTION_FRACTION}}
    problems = []
    if len(steps_out) != len(schedule):
        problems.append(f"result has {len(steps_out)} steps for a {len(schedule)}-step schedule")
    cumulative = 0.0
    for i, (dt, _fl) in enumerate(schedule):
        cumulative += dt
        if i < len(steps_out) and rel_diff(steps_out[i]["t_s"], cumulative) > 1e-12:
            problems.append(f"step {i + 1}: t_s {steps_out[i]['t_s']} vs schedule {cumulative}")
    unsupported = sorted(name_of(k) for k in keys if net.node(k)["fission"] != 0.0)
    out["nodes_with_fission_rows"] = unsupported
    if unsupported:
        problems.append(f"nodes with nonzero fission rows are not rebuilt by this checker: {unsupported}")
    conservation = {}
    for key in sorted(keys):
        gap = net.conservation(key)
        if gap is not None:
            conservation[name_of(key)] = gap
    out["conservation_gap_loss_vs_products"] = {
        "note": "diagnostic only: relative gap between the ZAP=-1 loss rows and the product rows (leak included)",
        "worst": max(conservation.values(), default=0.0),
        "worst_nuclides": dict(sorted(conservation.items(), key=lambda kv: -kv[1])[:5])}

    counts = {"strict": 0, "resolution_only": 0, "fail": 0}
    per_target, failures = [], []
    for target in routes["targets"]:
        worst_final, worst_any, n_routes = 0.0, 0.0, 0
        for path in target["paths"]:
            nodes = [key_of(n) for n in path["nuclides"]]
            n_routes += 1
            by_step = path.get("atoms_per_g_by_step")
            if not by_step or len(by_step) != len(schedule):
                problems.append(f"{target['target']}: route {path['nuclides']} lacks atoms_per_g_by_step")
                continue
            diag, sub = [], []
            for fl in mults:
                row = []
                for i, node in enumerate(nodes):
                    row.append(0.0 if (i == 0 and mode == "trace") else net.removal(node, fl))
                diag.append(row)
                sub.append([net.edge(nodes[i], nodes[i + 1], fl) for i in range(len(nodes) - 1)])
            if any(all(sub[s][i] == 0.0 for s in range(len(schedule))) for i in range(len(nodes) - 1)):
                problems.append(f"{target['target']}: route {path['nuclides']} has an edge with no rate in any step")
            initial = bulk.get(nodes[0])
            if initial is None:
                problems.append(f"{target['target']}: route source {path['nuclides'][0]} is not a material nuclide")
                continue
            mine, peak = integrate_chain(diag, sub, dts, initial)
            for s, (got, want) in enumerate(zip(by_step, mine)):
                verdict, rel = compare_value(got, want, peak)
                counts[verdict] += 1
                worst_any = max(worst_any, rel if math.isfinite(rel) else 1e300)
                if verdict == "fail":
                    failures.append({"target": target["target"], "route": path["nuclides"], "step": s + 1,
                                     "reported": got, "recomputed": want, "relative": rel, "chain_peak": peak})
            verdict, rel = compare_value(path["atoms_per_g"], mine[-1], peak)
            counts[verdict] += 1
            worst_final = max(worst_final, rel if math.isfinite(rel) else 1e300)
            if verdict == "fail":
                failures.append({"target": target["target"], "route": path["nuclides"], "step": "final",
                                 "reported": path["atoms_per_g"], "recomputed": mine[-1], "relative": rel,
                                 "chain_peak": peak})
            if rel_diff(path["atoms_per_g"], by_step[-1]) > 0.0:
                problems.append(f"{target['target']}: route {path['nuclides']} atoms_per_g differs from its last "
                                "atoms_per_g_by_step entry")
        per_target.append({"target": target["target"], "routes": n_routes, "worst_relative_final": worst_final,
                           "worst_relative_any_step": worst_any})
    out["comparisons"] = counts
    out["per_target"] = per_target
    out["failures"] = failures[:MAX_LISTED]
    out["n_failures"] = len(failures)
    out["problems"] = problems
    missing = sorted(set(TARGETS) - {t["target"] for t in routes["targets"]})
    if missing:
        problems.append(f"targets missing from the result: {missing}")
    out["pass"] = bool(not failures and not problems and sum(counts.values()) > 0)
    out["conventions"] = ("see the module docstring: full removal = lambda + multiplier * sum of ZAP=-1 rows; edge "
                          "rate = all decay branches + reaction rows between the pair; trace reservoir = bulk atoms "
                          "x per-atom edge rate with a zero diagonal")
    return out


def gate_g4(result, spec):
    routes = result["pathway_routes"]
    inventory = {e["nuclide"]: e["atoms_per_g"] for e in result["steps"][-1]["inventory"]}
    inventory_total = math.fsum(inventory.values())
    material_atoms = math.fsum(bulk_atoms_per_g(spec, load_isotopes()).values())
    out = {"final_inventory_atoms_per_g": inventory_total, "material_atoms_per_g": material_atoms,
           "reading": ("`total atoms` for the 1e-6 significance cut is the sum of the final inventory's tracked "
                       "atoms (in trace mode the constant bulk is not in the inventory); against the material's "
                       "own atoms every target here would be below 1e-6 and the explained-fraction test vacuous"),
           "closure_tolerance": CLOSURE_TOL, "explained_min": EXPLAINED_MIN, "targets": []}
    bad = []
    for t in routes["targets"]:
        name = t["target"]
        total = t["total_atoms_per_g"]
        listed = math.fsum(p["atoms_per_g"] for p in t["paths"])
        closure = rel_diff(listed + t["residual_atoms_per_g"], total)
        sum_vs_report = rel_diff(listed, t["paths_atoms_per_g"])
        inv = inventory.get(name)
        total_vs_inventory = None if inv is None else rel_diff(total, inv)
        share = total / inventory_total if inventory_total > 0.0 else 0.0
        significant = share >= SIGNIFICANT_TARGET_SHARE
        explained = t["explained_fraction"]
        loops = math.fsum(p["atoms_per_g"] for p in t["paths"] if p["is_loop"])
        entry = {"target": name, "total_atoms_per_g": total, "inventory_atoms_per_g": inv,
                 "routes": len(t["paths"]), "routes_dropped_by_cap": t.get("paths_dropped_by_cap"),
                 "closure_relative": closure, "listed_sum_vs_reported_sum": sum_vs_report,
                 "total_vs_inventory": total_vs_inventory, "explained_fraction": explained,
                 "explained_fraction_recomputed": listed / total if total > 0.0 else None,
                 "share_of_final_inventory": share, "significant": significant,
                 "loop_sum_vs_reported": rel_diff(loops, t["loop_atoms_per_g"])}
        ok = closure <= CLOSURE_TOL and sum_vs_report <= CLOSURE_TOL
        ok = ok and inv is not None and total_vs_inventory <= CLOSURE_TOL
        ok = ok and (not significant or explained >= EXPLAINED_MIN)
        entry["pass"] = bool(ok)
        if not ok:
            bad.append(name)
        out["targets"].append(entry)
    out["failed_targets"] = bad
    out["pass"] = bool(not bad and out["targets"])
    return out


def route_key(path):
    return (path["source"], tuple(path["nuclides"]), tuple(path["edges"]))


def gate_g5(declared, other, declared_spec):
    mode_d, mode_o = declared.get("mode"), other.get("mode")
    out = {"declared_mode": mode_d, "other_mode": mode_o,
           "rule": ("a route present in both modes passes if its final value and every by-step value agree at 1e-8 "
                    "relative, or differ by no more than the coupled run's numerical_floor_atoms_per_g at that step "
                    "(the check_p122.py coupled-floor rule). A route present in one mode only must carry a fraction "
                    f"below {UNMATCHED_ROUTE_SLACK} x path_floor."),
           "targets": [], "unmatched": [], "bad": []}
    if {mode_d, mode_o} != {"trace", "coupled"}:
        out["error"] = f"expected one trace and one coupled run, got {mode_d!r} and {mode_o!r}"
        out["pass"] = False
        return out
    coupled = declared if mode_d == "coupled" else other
    floors = [s["numerical_floor_atoms_per_g"] for s in coupled["steps"]]
    n_strict = n_floor = 0
    floor_cap = PATH_FLOOR * UNMATCHED_ROUTE_SLACK
    by_target_o = {t["target"]: t for t in other["pathway_routes"]["targets"]}
    for t in declared["pathway_routes"]["targets"]:
        name = t["target"]
        to = by_target_o.get(name)
        if to is None:
            out["bad"].append({"target": name, "note": "missing in the other mode"})
            continue
        map_d = {route_key(p): p for p in t["paths"]}
        map_o = {route_key(p): p for p in to["paths"]}
        worst = 0.0
        for k, pd in map_d.items():
            po = map_o.get(k)
            if po is None:
                continue
            series_d = list(pd["atoms_per_g_by_step"]) + [pd["atoms_per_g"]]
            series_o = list(po["atoms_per_g_by_step"]) + [po["atoms_per_g"]]
            for s, (a, b) in enumerate(zip(series_d, series_o)):
                floor = floors[min(s, len(floors) - 1)]
                rel = rel_diff(a, b)
                worst = max(worst, rel)
                if rel <= TRACE_COUPLED_REL_TOL:
                    n_strict += 1
                elif abs(a - b) <= floor:
                    n_floor += 1
                else:
                    out["bad"].append({"target": name, "route": list(k[1]), "step_index": s, "declared": a,
                                       "other": b, "relative": rel, "abs_diff": abs(a - b), "floor": floor})
        for label, mine, theirs in (("declared_only", map_d, map_o), ("other_only", map_o, map_d)):
            for k, p in mine.items():
                if k not in theirs:
                    entry = {"target": name, "where": label, "route": list(k[1]), "fraction": p["fraction"]}
                    out["unmatched"].append(entry)
                    if p["fraction"] > floor_cap:
                        out["bad"].append({**entry, "note": "unmatched route above the threshold slack"})
        out["targets"].append({"target": name, "routes_declared": len(map_d), "routes_other": len(map_o),
                               "routes_in_both": len(set(map_d) & set(map_o)), "worst_relative_difference": worst,
                               "total_declared": t["total_atoms_per_g"], "total_other": to["total_atoms_per_g"],
                               "total_relative_difference": rel_diff(t["total_atoms_per_g"], to["total_atoms_per_g"]),
                               "explained_declared": t["explained_fraction"],
                               "explained_other": to["explained_fraction"]})
    out["comparisons_within_1e-8"] = n_strict
    out["comparisons_within_floor_only"] = n_floor
    out["coupled_floor_atoms_per_g_final_step"] = floors[-1]
    out["unmatched"] = out["unmatched"][:MAX_LISTED]
    out["bad"] = out["bad"][:MAX_LISTED]
    out["pass"] = bool(not out["bad"] and out["targets"] and n_strict + n_floor > 0)
    return out


# ------------------------------------------------------------------------------ G6: FISPACT-II comparison

PATH_LINE = re.compile(r"^\s*path\s+(\d+)\s+([0-9.]+)%\s+(.*)$")
TARGET_LINE = re.compile(r"^\s*Target nuclide\s+([A-Z][a-z]?)\s+(\d+)(m\d?)?\s+([0-9.]+)% of inventory given by\s+"
                         r"(\d+)\s+path")
EDGE_NUCLIDE = re.compile(r"([A-Z][a-z]?)\s+(\d+)(m\d?)?\s*---\(([A-Za-z])\)---")
REACTION_SHARE = re.compile(r"([0-9.]+)%(\([^)\s]*\)|\S+)")
ATOMS_LINE = re.compile(r"^\s*([A-Z][a-z]?)\s+(\d+)(m\d?)?\s+([0-9.]+E[-+]\d+)\s+([0-9.]+E[-+]\d+)")


def fispact_name(symbol, mass, meta):
    base = f"{symbol}{int(mass)}"
    if not meta:
        return base
    return base + ("m1" if meta == "m" else meta)


def parse_fispact_pathways(path):
    """The irradiation-phase pathway section of a FISPACT-II .out: settings, source/target lists, and per target
    its paths (nuclides, edge letters, percentage, reaction shares). Also the atoms of the uncertainty table."""
    lines = Path(path).read_text(errors="replace").splitlines()
    start = next((i for i, line in enumerate(lines) if "P A T H W A Y" in line), None)
    if start is None:
        return {"found": False}
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^1\s+\*\s+\*\s+\*\s+TIME INTERVAL", lines[i])),
               len(lines))
    out = {"found": True, "first_line": start + 1, "last_line": end, "targets": [], "settings": {}}
    block = lines[start:end]
    for line in block:
        for label, pattern in (("path_floor_percent", r"path floor\s*=\s*([0-9.E+-]+)%"),
                               ("loop_floor_percent", r"loop floor\s*=\s*([0-9.E+-]+)%"),
                               ("max_depth", r"max depth\s*=\s*(\d+)"),
                               ("flux", r"flux\s*=\s*([0-9.E+-]+)"),
                               ("irradiation_s", r"irradiation time\s*=\s*([0-9.E+-]+)")):
            m = re.search(pattern, line)
            if m and label not in out["settings"]:
                out["settings"][label] = float(m.group(1))
    for label, header in (("sources", "Source Nuclides"), ("target_list", "Target Nuclides")):
        for i, line in enumerate(block):
            if line.strip() == header:
                names, j = [], i + 1
                while j < len(block) and block[j].strip():
                    names += [fispact_name(m[0], m[1], m[2]) for m in
                              re.findall(r"([A-Z][a-z]?)\s+(\d+)(m\d?)?", block[j])]
                    j += 1
                out[label] = names
                break
    current = None
    for i, line in enumerate(block):
        m = TARGET_LINE.match(line)
        if m:
            current = {"target": fispact_name(m.group(1), m.group(2), m.group(3)),
                       "percent_of_inventory": float(m.group(4)), "n_paths": int(m.group(5)), "paths": []}
            out["targets"].append(current)
            continue
        m = PATH_LINE.match(line)
        if m and current is not None:
            found = EDGE_NUCLIDE.findall(m.group(3))
            if not found:
                continue
            path = {"number": int(m.group(1)), "percent": float(m.group(2)),
                    "nuclides": [fispact_name(s, a, meta) for s, a, meta, _e in found],
                    "edge_types": [e for _s, _a, _m, e in found[:-1]],
                    "lifetime_flag": found[-1][3], "reactions": []}
            j = i + 1
            while j < len(block) and block[j].strip() and not PATH_LINE.match(block[j]) and \
                    not TARGET_LINE.match(block[j]):
                path["reactions"] += [{"percent": float(share), "label": label}
                                      for share, label in REACTION_SHARE.findall(block[j])]
                j += 1
            current["paths"].append(path)
    atoms = {}
    for line in lines[:start]:
        m = ATOMS_LINE.match(line)
        if m:
            atoms.setdefault(fispact_name(m.group(1), m.group(2), m.group(3)), float(m.group(4)))
    out["uncertainty_table_atoms"] = atoms
    return out


def g6_compare(result, fispact_path):
    """Tabulate the FISPACT-II paths against the ACTINV routes; report only."""
    parsed = parse_fispact_pathways(fispact_path)
    out = {"report_only": True, "fispact_file": str(fispact_path), "fispact": {k: v for k, v in parsed.items()
                                                                               if k != "targets"}}
    if not parsed.get("found"):
        out["note"] = "no pathway section in the FISPACT-II output"
        return out
    by_name = {t["target"]: t for t in parsed["targets"]}
    out["fispact_targets"] = [{"target": t["target"], "percent_of_inventory": t["percent_of_inventory"],
                               "n_paths": t["n_paths"]} for t in parsed["targets"]]
    rows = []
    # FISPACT-II's pathway section analyses the irradiation phase, so ACTINV's end-of-irradiation share (the first
    # step's by-step value over the first step's inventory) is tabulated beside the end-of-schedule share
    first_inventory = {e["nuclide"]: e["atoms_per_g"] for e in result["steps"][0]["inventory"]}
    for t in result["pathway_routes"]["targets"]:
        f = by_name.get(t["target"])
        mine = {tuple(p["nuclides"]): p for p in t["paths"]}
        irr_total = first_inventory.get(t["target"])
        entry = {"target": t["target"], "actinv_total_atoms_per_g": t["total_atoms_per_g"],
                 "actinv_explained_fraction": t["explained_fraction"], "actinv_routes": len(t["paths"]),
                 "actinv_top": [{"route": p["nuclides"], "edges": p["edges"], "fraction": p["fraction"],
                                 "is_loop": p["is_loop"]} for p in t["paths"][:5]],
                 "fispact_found": f is not None}
        entry["actinv_atoms_per_g_end_of_irradiation"] = irr_total
        if irr_total and t["paths"] and t["paths"][0].get("atoms_per_g_by_step"):
            entry["actinv_explained_fraction_end_of_irradiation"] = math.fsum(
                p["atoms_per_g_by_step"][0] for p in t["paths"]) / irr_total
        if f is not None:
            entry["fispact_percent_of_inventory"] = f["percent_of_inventory"]
            entry["fispact_atoms_uncertainty_table"] = parsed["uncertainty_table_atoms"].get(t["target"])
            entry["paths"] = []
            for p in f["paths"]:
                match = mine.get(tuple(p["nuclides"]))
                entry["paths"].append({
                    "fispact_nuclides": p["nuclides"], "fispact_edge_types": p["edge_types"],
                    "fispact_percent": p["percent"],
                    "fispact_reactions": [f"{r['percent']:.2f}% {r['label']}" for r in p["reactions"]],
                    "actinv_edges": match["edges"] if match else None,
                    "actinv_percent": 100.0 * match["fraction"] if match else None,
                    "actinv_percent_end_of_irradiation": (
                        100.0 * match["atoms_per_g_by_step"][0] / irr_total
                        if match and irr_total and match.get("atoms_per_g_by_step") else None),
                    "actinv_matches_route": match is not None})
        rows.append(entry)
    out["targets"] = rows
    out["note"] = ("report only, no verdict: FISPACT-II ran TENDL-2017 cross sections, ACTINV the library of the "
                   "spec (TENDL-2025); FISPACT-II's path floor is 0.5 % where this run uses 1e-4 %; percentages are "
                   "each code's own share of its own target inventory. FISPACT-II analyses the irradiation phase, "
                   "so ACTINV's end-of-irradiation share (step 1) is the like-for-like column; the end-of-schedule "
                   "share is also given")
    return out


def print_g6(g6):
    print("  G6 (report only): FISPACT-II path vs ACTINV route, percent of target inventory")
    for entry in g6.get("targets", []):
        print(f"   {entry['target']}: ACTINV explained {100 * entry['actinv_explained_fraction']:.3f} % by "
              f"{entry['actinv_routes']} routes; FISPACT-II {entry.get('fispact_percent_of_inventory', 'n/a')} %")
        for p in entry.get("paths", []):
            end = p["actinv_percent"]
            irr = p["actinv_percent_end_of_irradiation"]
            print(f"     {' > '.join(p['fispact_nuclides'])}: FISPACT-II {p['fispact_percent']:.3f} %, ACTINV "
                  f"{'-' if irr is None else f'{irr:.3f}'} % at end of irradiation, "
                  f"{'-' if end is None else f'{end:.3f}'} % at end of schedule")


# ------------------------------------------------------------------------------ self-check (no binary)


def self_check():
    """Offline unit checks of the independent integrator, parsers and rate rules."""
    failures = []

    def expect(cond, what):
        if not cond:
            failures.append(what)

    mpmath.mp.dps = MP_DPS
    # two-node chain, distinct rates: N1 = N0 a (e^-at - e^-bt) / (b - a)
    a, b, n0, t = 1.0e-3, 4.0e-4, 1.0e20, 3000.0
    got, _ = integrate_chain([[a, b]], [[a]], [t], n0)
    want = n0 * a * (math.exp(-a * t) - math.exp(-b * t)) / (b - a)
    expect(rel_diff(got[-1], want) < 1e-12, f"two-node Bateman: {got[-1]} vs {want}")
    # equal rates: N1 = N0 a t e^-at
    got, _ = integrate_chain([[a, a]], [[a]], [t], n0)
    expect(rel_diff(got[-1], n0 * a * t * math.exp(-a * t)) < 1e-12, "equal-rate chain")
    # three nodes with a branch loss on the middle node, two steps
    r = [(2e-3, 1e-3, 5e-4), (2e-3, 3e-3, 0.0)]
    got, _ = integrate_chain([[x[0], x[1], x[2]] for x in r], [[1e-3, 5e-4]] * 2, [100.0, 400.0], 1.0)
    expect(len(got) == 2 and got[-1] > 0.0, "multi-step chain")
    # trace reservoir: constant source, one node: N = (s/l)(1 - e^-lt)
    s_rate, lam = 3.0e-3, 2.0e-3
    got, _ = integrate_chain([[0.0, lam]], [[s_rate]], [500.0], 7.0)
    expect(rel_diff(got[-1], 7.0 * s_rate / lam * (1 - math.exp(-lam * 500.0))) < 1e-12, "constant reservoir")
    # parsers
    expect(endf_float(" 1.234567+3") == 1234.567 and endf_float("-2.5-2") == -0.025, "ENDF float")
    expect(rtyp_digits(1.5) == [1, 5] and rtyp_digits(3.0) == [3] and rtyp_digits(4.0) == [4], "RTYP digits")
    expect(key_of("Mn58m1") == (25058, 1) and name_of((25058, 1)) == "Mn58m1" and key_of("Fe56") == (26056, 0),
           "names")
    expect(parse_duration("300.0 s") == 300.0 and parse_duration("1e-8 s") == 1e-8 and parse_duration("5 min") == 300,
           "durations")
    out, n = mask_ms(b'{"a": 1,\n  "ms": 883.447943\n}')
    expect(n == 1 and out == b'{"a": 1,\n  "ms": 0\n}', "ms mask")
    expect(compare_value(1.0 + 1e-9, 1.0, 1.0)[0] == "strict" and compare_value(1.0 + 1e-3, 1.0, 1e10)[0] ==
           "resolution_only" and compare_value(1.0 + 1e-3, 1.0, 1.0)[0] == "fail", "compare rule")
    # network rules on a synthetic parse
    records = {(1001, 0): {"nst": 0, "half_life": 100.0, "modes": [(1.0, 0.0, 0.6), (3.0, 0.0, 0.4)]}}
    chain = {(1001, 0), (2001, 0)}
    net = Network(records, chain, None, np.zeros(1))
    node = net.node((1001, 0))
    lam1 = math.log(2.0) / 100.0
    expect(abs(node["decay"].get((2001, 0), 0.0) - 0.6 * lam1) < 1e-18 and node["decay_leak"] > 0.0,
           "beta- daughter and self-IT leak")
    # FISPACT parser on a miniature section
    sample = ("0   Nuclide     Atoms\n    Mn 56    3.08469E+09  6.81E+08\n"
              " P A T H W A Y   A N A L Y S I S\n path floor    =  5.00000E-01% of target inventory\n"
              " Target nuclide Mn 56    100.000% of inventory given by  2 paths\n"
              " path  1  99.415% Fe 56 ---(R)--- Mn 56 ---(L)--- \n                    100.00%(n,p)       \n\n"
              " path  2   0.585% Fe 57 ---(R)--- Mn 56 ---(L)--- \n                     87.63%(n,np)  12.37%(n,d)\n"
              "1 * * * TIME INTERVAL   3 * * *\n")
    tmp = ROOT / "target" / "p127-selfcheck.out"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(sample)
    try:
        parsed = parse_fispact_pathways(tmp)
    finally:
        tmp.unlink()
    ok = parsed.get("found") and len(parsed["targets"]) == 1 and len(parsed["targets"][0]["paths"]) == 2
    expect(ok, "FISPACT section parse")
    if ok:
        p = parsed["targets"][0]["paths"]
        expect(p[0]["nuclides"] == ["Fe56", "Mn56"] and p[0]["percent"] == 99.415, "FISPACT path 1")
        expect([r["label"] for r in p[1]["reactions"]] == ["(n,np)", "(n,d)"], "FISPACT reaction shares")
        expect(parsed["uncertainty_table_atoms"].get("Mn56") == 3.08469e9, "FISPACT atoms table")
    for failure in failures:
        print(f"self-check FAIL: {failure}")
    print(f"P127 self-check: {'PASS' if not failures else 'FAIL'}")
    return 0 if not failures else 1


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", help="release binary built from the P127 base commit (2f3daa4)")
    ap.add_argument("--candidate", help="release binary built from the P127 implementation commit")
    ap.add_argument("--fispact-out", default=str(Path("~/nuclear-data/conderc-fns/fns/Fe/"
                                                     "TENDL-2017_1996exp_5min.out").expanduser()))
    ap.add_argument("--workdir", default="target/p127-work")
    ap.add_argument("--out", default="results/p127_pathways.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    ap.add_argument("--self-check", action="store_true", help="offline unit checks; runs no binary")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
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

    record = {"protocol": "ACTINV-P127", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "isotope_table": record_file(TABLES_RS),
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
        return out_file

    def evaluate(name, function, *call_args):
        try:
            record["gates"][name] = function(*call_args)
        except (GateError, OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError,
                zipfile.BadZipFile) as error:
            record["gates"][name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}

    # ---- G2
    ref_file = execute("g2_reference", args.reference, GATE_SPEC, ROOT)
    cand_file = execute("g2_candidate", args.candidate, GATE_SPEC, ROOT)
    if ref_file is not None and cand_file is not None:
        evaluate("G2", gate_g2, ref_file, cand_file, spec_ok)
    else:
        record["gates"]["G2"] = {"pass": False, "error": "a gate run failed; see runs"}

    # ---- G3, G4 (declared mode) and G5 (the other mode)
    declared_path = workdir / "declared.spec.json"
    declared_spec = pathways_spec(base_spec, None, "pathways, declared mode")
    record["data_inputs"] = data_inputs(declared_spec)
    declared_path.write_text(json.dumps(declared_spec, indent=1) + "\n")
    declared_file = execute("pathways_declared", args.candidate, declared_path, workdir)
    declared = json.loads(declared_file.read_text()) if declared_file is not None else None
    other = None
    mode_info = {"declared": (base_spec.get("options") or {}).get("mode", "auto")}
    if declared is not None and declared.get("mode") in ("trace", "coupled"):
        mode_info["declared_resolved"] = declared["mode"]
        mode_info["other"] = "coupled" if declared["mode"] == "trace" else "trace"
        other_path = workdir / "other.spec.json"
        other_path.write_text(json.dumps(pathways_spec(base_spec, mode_info["other"],
                                                       f"pathways, {mode_info['other']} mode"), indent=1) + "\n")
        other_file = execute("pathways_other", args.candidate, other_path, workdir)
        other = json.loads(other_file.read_text()) if other_file is not None else None
    record["mode_selection"] = mode_info
    if declared is not None and declared.get("pathway_routes") is not None:
        evaluate("G3", gate_g3, declared, declared_spec)
        evaluate("G4", gate_g4, declared, declared_spec)
    else:
        reason = "the pathways run failed or returned no pathway_routes; see runs"
        record["gates"]["G3"] = {"pass": False, "error": reason}
        record["gates"]["G4"] = {"pass": False, "error": reason}
    if declared is not None and other is not None and other.get("pathway_routes") is not None:
        evaluate("G5", gate_g5, declared, other, declared_spec)
    else:
        record["gates"]["G5"] = {"pass": False, "error": "the other-mode pathways run failed; see runs"}

    # ---- G6 (report only)
    fispact = Path(args.fispact_out)
    if declared is not None and declared.get("pathway_routes") is not None and fispact.exists():
        record["fispact_out"] = record_file(fispact)
        try:
            record["gates"]["G6"] = g6_compare(declared, fispact)
        except (OSError, ValueError, KeyError, IndexError, AttributeError) as error:
            record["gates"]["G6"] = {"report_only": True, "error": f"{type(error).__name__}: {error}"}
    else:
        record["gates"]["G6"] = {"report_only": True, "skipped": "no pathways run or FISPACT-II output not found"}

    gates = ("G2", "G3", "G4", "G5")
    overall = all(bool(record["gates"].get(g, {}).get("pass")) for g in gates)
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in gates:
        g = record["gates"][gate]
        print(f"P127 {gate}: {'PASS' if g.get('pass') else 'FAIL'}" + (f" ({g['error']})" if "error" in g else ""))
    g3 = record["gates"]["G3"]
    if "comparisons" in g3:
        print(f"  G3: comparisons {g3['comparisons']}, {g3['n_failures']} failures, problems {g3['problems'][:3]}")
    if "targets" in record["gates"]["G6"] and "error" not in record["gates"]["G6"]:
        print_g6(record["gates"]["G6"])
    print(f"P127 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
