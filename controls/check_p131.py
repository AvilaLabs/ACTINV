#!/usr/bin/env python3
"""P131 gates G2, G3, G4 and G5 (protocols/ACTINV-P131_PROTOCOL.md): `actinv inspect` against independent parses.

  G2  `inspect decay` for Co60, Cs137, Am241, Cf252, Tc99m and Fe55 against this script's own ENDF-6 MF=8 MT=457
      parser: half-life, branching ratios, mean energies and Q values exactly as stored, daughters from the RTYP
      digit rule, total discrete gamma energy per decay (sum of FD*RI*E over STYP=0 lines) within 1e-12 relative.
  G3  `inspect xs --target Fe56 --spectrum SPEC` against an independent NumPy collapse of the library NPZ
      (sum(sigma_g*phi_g)/sum(phi_g)) within 1e-12 relative, and rate = sigma_bar * 1e-24 * sum(phi_g).
  G4  The solver result for the same spec is searched for a per-reaction rate or collapsed cross section. If the
      result carries one for Fe56 (n,p) it is compared with the inspect rate; if no such field exists the gate is
      reported not applicable, with the reason and the searched keys.
  G5  `inspect yields` (independent, and --cumulative) for U-235 against this script's own MF=8 MT=454/459 parser
      within 1e-12 relative for every product, including energies between tabulated points (the linear rule in
      crates/actinv-data/src/fission.rs) and clamped energies.

Group-ordering convention (G3). The NPZ library stores groups in ascending energy: group g spans bounds[g] ..
bounds[g+1], and sig[row, g] belongs to it. A run spec lists flux_per_group highest-energy first when
`spectrum.descending` is true (FISPACT fluxes files). `inspect` and the solver reverse such a list to ascending order,
then scale it to `spectrum.total` when given (every flux times total/sum), before the collapse. This script mirrors
that: flux_ascending = reversed(flux_per_group) if descending, then * total/sum(flux). The `inspect groups` boundaries
for the spectrum's structure are compared bitwise with the NPZ bounds, which pins the ascending order of both.

Usage:
  check_p131.py --candidate BIN [--decay PATH] [--yields PATH] [--library NPZ] [--spec examples/fns_fe_5min.json]
      [--work target/p131] [--out results/p131_inspect.json] [--reuse]

Defaults: the decay file is `decay.primary` and the library `library.path` of the spec (relative to the repository
root); the yields file is the ENDF/B-VIII.0 nfpy U-235 evaluation ~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf.
Run inside the required systemd cgroup with a disk-backed TMPDIR; the binary is run serially, with no shell. Exit 0
only when G2, G3, G5 pass and G4 passes or is documented not applicable.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TOL = 1e-12
INSPECT_TIMEOUT_S = 900
RUN_TIMEOUT_S = 7200
DEFAULT_SPEC = "examples/fns_fe_5min.json"
DEFAULT_YIELDS = "~/nuclear-data/endfb-viii.0-nfpy/nfy-092_U_235.endf"

DECAY_NUCLIDES = ["Co60", "Cs137", "Am241", "Cf252", "Tc99m", "Fe55"]
# Input name -> (ZA, LISO). Metastable states are written `m` (LISO 1) or `mN` (LISO N); inspect prints `m<LISO>`.
DECAY_KEYS = {"Co60": (27060, 0), "Cs137": (55137, 0), "Am241": (95241, 0), "Cf252": (98252, 0),
              "Tc99m": (43099, 1), "Fe55": (26055, 0)}

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr "
    "Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir "
    "Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl "
    "Mc Lv Ts Og").split()
SYMBOL = {z: s for z, s in enumerate(ELEMENTS, start=1)}

# RTYP digit -> (dZ, dA). 3 is isomeric transition (same nucleus); 6 (SF) and unknown digits have no daughter.
DECAY_STEP = {1: (1, 0), 2: (-1, 0), 3: (0, 0), 4: (-2, -4), 5: (0, -1), 7: (-1, -1)}


class GateError(Exception):
    """A gate could not be evaluated (as opposed to evaluating to a mismatch)."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel_diff(a, b):
    """Relative difference; infinite when either side is missing or not a finite number, so it never passes."""
    if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)):
        return math.inf
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def nuclide_label(za, liso):
    """Name as `inspect` prints it: symbol, mass number, and `m<LISO>` for an excited state."""
    symbol = SYMBOL[za // 1000]
    return f"{symbol}{za % 1000}m{liso}" if liso > 0 else f"{symbol}{za % 1000}"


# ---------------------------------------------------------------- ENDF-6 reading (independent of the repository)


_FLOAT = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))([+-]\d+)")


def efloat(field):
    """ENDF float field: blank is zero; `1.234567+8` means 1.234567e+8. One correctly rounded conversion."""
    s = field.strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        pass
    m = _FLOAT.fullmatch(s)
    if not m:
        raise GateError(f"unparseable ENDF float field {field!r}")
    return float(m.group(1) + "e" + m.group(2))


def eint(field):
    s = field.strip()
    return int(s) if s else 0


def tail(line):
    """(MAT, MF, MT) from columns 67-75, or None for a line without them (TPID, blank, short)."""
    if len(line) < 75:
        return None
    try:
        return eint(line[66:70]), eint(line[70:72]), eint(line[72:75])
    except ValueError:
        return None


class Records:
    """Sequential reader over the lines of one ENDF section (a HEAD, then CONT/LIST/TAB1 records)."""

    def __init__(self, lines):
        self.lines = lines
        self.i = 0

    def cont(self):
        if self.i >= len(self.lines):
            raise GateError("section ended inside a record")
        line = self.lines[self.i]
        self.i += 1
        f = [line[11 * k:11 * (k + 1)] for k in range(6)]
        return efloat(f[0]), efloat(f[1]), eint(f[2]), eint(f[3]), eint(f[4]), eint(f[5])

    def floats(self, count):
        values = []
        for _ in range(-(-count // 6)):
            if self.i >= len(self.lines):
                raise GateError("section ended inside a LIST payload")
            line = self.lines[self.i]
            self.i += 1
            values.extend(efloat(line[11 * k:11 * (k + 1)]) for k in range(6))
        return values[:count]

    def lst(self):
        """LIST record: ((C1, C2, L1, L2, N1, N2), values[N1])."""
        head = self.cont()
        return head, self.floats(head[4])

    def tab1(self):
        """TAB1 record: head, then NR interpolation pairs and NP points (three pairs/points per line)."""
        head = self.cont()
        nr, npts = head[4], head[5]
        skipped = -(-nr // 3) + -(-npts // 3)
        if self.i + skipped > len(self.lines):
            raise GateError("section ended inside a TAB1 record")
        self.i += skipped
        return head

    def done(self):
        return self.i == len(self.lines)


def sections(path, wanted):
    """{(mat, mf, mt): [lines]} for the (mf, mt) pairs in `wanted`; each run of consecutive lines is one section."""
    text = Path(path).read_text(encoding="ascii", errors="strict")
    out = {}
    run_key, run = None, []
    for line in text.splitlines():
        t = tail(line)
        key = t if t is not None and (t[1], t[2]) in wanted else None
        if key != run_key:
            if run_key is not None:
                if run_key in out:
                    raise GateError(f"duplicate section {run_key} in {path}")
                out[run_key] = run
            run_key, run = key, []
        if key is not None:
            run.append(line)
    if run_key is not None:
        if run_key in out:
            raise GateError(f"duplicate section {run_key} in {path}")
        out[run_key] = run
    return out


def parse_decay_section(lines):
    r = Records(lines)
    za_f, awr, lis, liso, nst, nsp = r.cont()
    head_e, energies = r.lst()                     # T1/2, dT1/2, ..., then E_i, dE_i pairs
    head_m, mode_values = r.lst()                  # SPI, PAR, ..., NDK modes of 6 values
    ndk = head_m[5]
    modes = []
    for k in range(ndk):
        rtyp, rfs, q, dq, br, dbr = mode_values[6 * k:6 * k + 6]
        modes.append({"rtyp": rtyp, "rfs": rfs, "q": q, "dq": dq, "br": br, "dbr": dbr})
    spectra = []
    for _ in range(nsp):
        head, v = r.lst()
        styp, lcon, lcov, ner = head[1], head[2], head[3], head[5]
        at = lambda k: v[k] if k < len(v) else 0.0
        discrete = []
        if lcon != 1:
            for _ in range(ner):
                dh, dv = r.lst()
                discrete.append({"energy": dh[0], "intensity": dv[2] if len(dv) > 2 else 0.0})
        if lcon != 0:
            r.tab1()
        # Covariance records follow the spectrum as LIST records, flagged by LCOV (1/3 with a continuum, 2/3).
        if lcov in (1, 3) and lcon != 0:
            r.lst()
        if lcov in (2, 3):
            r.lst()
        spectra.append({"styp": int(round(styp)), "lcon": lcon, "fd": at(0), "average_energy": at(2),
                        "discrete": discrete})
    if not r.done():
        raise GateError(f"{len(lines) - r.i} unconsumed record(s) after the last spectrum")
    return {"za": int(round(za_f)), "liso": liso, "nst": nst, "half_life": head_e[0], "d_half_life": head_e[1],
            "energies": energies, "modes": modes, "spectra": spectra}


def decay_daughter(za, rtyp, rfs):
    """(ZA, LISO) of the daughter of one mode, or None (SF, unknown digit, or a non-physical result)."""
    z, a = za // 1000, za % 1000
    digits = [int(c) for c in f"{rtyp:.6f}".rstrip("0").rstrip(".") if c.isdigit()]
    if not digits:
        return None
    for d in digits:
        if d not in DECAY_STEP:
            return None
        z += DECAY_STEP[d][0]
        a += DECAY_STEP[d][1]
    if z < 1 or a < 1:
        return None
    return z * 1000 + a, int(round(rfs))


def expected_decay(nuclide):
    stable = nuclide["nst"] == 1 or nuclide["half_life"] <= 0.0
    e = nuclide["energies"]
    lines = 0
    gamma = 0.0
    for sp in nuclide["spectra"]:
        if sp["styp"] != 0:
            continue
        lines += len(sp["discrete"])
        gamma += math.fsum(sp["fd"] * ln["intensity"] * ln["energy"] for ln in sp["discrete"])
    modes = []
    for m in nuclide["modes"]:
        d = decay_daughter(nuclide["za"], m["rtyp"], m["rfs"])
        modes.append({**m, "daughter": nuclide_label(*d) if d else None,
                      "daughter_za": d[0] if d else None, "daughter_liso": d[1] if d else None})
    return {
        "stable": stable,
        "half_life_s": None if stable else nuclide["half_life"],
        "half_life_uncertainty_s": None if stable else nuclide["d_half_life"],
        "decay_constant_per_s": 0.0 if stable else math.log(2.0) / nuclide["half_life"],
        "beta": e[0] if len(e) > 0 else 0.0,
        "gamma": e[2] if len(e) > 2 else 0.0,
        "alpha": e[4] if len(e) > 4 else 0.0,
        "modes": modes,
        "gamma_lines": lines,
        "gamma_energy": gamma,
        "spectra": [{"styp": sp["styp"], "discrete_lines": 0 if sp["lcon"] == 1 else len(sp["discrete"]),
                     "has_continuum": sp["lcon"] != 0, "average_energy_eV": sp["average_energy"]}
                    for sp in nuclide["spectra"]],
    }


# ---------------------------------------------------------------- running the binary


def run_cmd(argv, timeout, cwd=None, log=None):
    """Run without a shell; returns (returncode, stdout). stderr goes to `log` when given, else is captured."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise GateError(f"timeout after {timeout} s: {' '.join(str(a) for a in argv[:4])} ...")
    if log is not None:
        Path(log).write_bytes(proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr


def inspect_json(binary, args, work, label):
    code, out, err = run_cmd([binary, "inspect", *args], INSPECT_TIMEOUT_S, cwd=ROOT, log=work / f"{label}.log")
    if code != 0:
        raise GateError(f"inspect {args[0]} exited {code}: {err.decode(errors='replace').strip()[:300]}")
    try:
        return json.loads(out)
    except ValueError as error:
        raise GateError(f"inspect {args[0]} printed invalid JSON: {error}")


class Findings:
    """Mismatch collector: keeps the first few per check so the record stays readable."""

    def __init__(self, limit=12):
        self.items = []
        self.count = 0
        self.limit = limit

    def add(self, what, **detail):
        self.count += 1
        if len(self.items) < self.limit:
            self.items.append({"what": what, **detail})

    def record(self):
        return {"count": self.count, "first": self.items}


# ---------------------------------------------------------------- G2


def gate_g2(binary, decay_path, work):
    sec = sections(decay_path, {(8, 457)})
    index = {}
    for (mat, _, _), lines in sec.items():
        head = Records(lines).cont()
        key = (int(round(head[0])), head[3])
        if key in index:
            raise GateError(f"duplicate decay record for ZA={key[0]} LISO={key[1]}")
        index[key] = lines
    report = inspect_json(binary, ["decay", decay_path, *DECAY_NUCLIDES], work, "g2_decay")
    out = {"nuclides_requested": DECAY_NUCLIDES, "count_reported": report.get("count"), "per_nuclide": {}}
    got = {(e["za"], e["liso"]): e for e in report.get("nuclides", [])}
    ok = report.get("schema") == "actinv-inspect-decay-1" and report.get("count") == len(DECAY_NUCLIDES)
    out["schema_ok"] = ok
    for name in DECAY_NUCLIDES:
        key = DECAY_KEYS[name]
        entry = {"za": key[0], "liso": key[1], "mismatches": None}
        out["per_nuclide"][name] = entry
        f = Findings()
        if key not in index:
            raise GateError(f"{name} (ZA={key[0]} LISO={key[1]}) is absent from {decay_path}")
        nuc = parse_decay_section(index[key])
        want = expected_decay(nuc)
        have = got.get(key)
        if have is None:
            f.add("nuclide missing from inspect output")
            entry["mismatches"] = f.record()
            continue
        exact = lambda label, a, b: None if a == b else f.add(label, inspect=a, independent=b)
        exact("nuclide name", have.get("nuclide"), nuclide_label(*key))
        exact("stable", have.get("stable"), want["stable"])
        exact("half_life_s", have.get("half_life_s"), want["half_life_s"])
        exact("half_life_uncertainty_s", have.get("half_life_uncertainty_s"), want["half_life_uncertainty_s"])
        lam = have.get("decay_constant_per_s")
        if lam is None or rel_diff(lam, want["decay_constant_per_s"]) > TOL:
            f.add("decay_constant_per_s", inspect=lam, independent=want["decay_constant_per_s"])
        me = have.get("mean_energy_eV", {})
        for field in ("beta", "gamma", "alpha"):
            exact(f"mean_energy_eV.{field}", me.get(field), want[field])
        modes = have.get("modes", [])
        if len(modes) != len(want["modes"]):
            f.add("mode count", inspect=len(modes), independent=len(want["modes"]))
        for k, (hm, wm) in enumerate(zip(modes, want["modes"])):
            exact(f"modes[{k}].rtyp", hm.get("rtyp"), wm["rtyp"])
            exact(f"modes[{k}].branching_ratio", hm.get("branching_ratio"), wm["br"])
            exact(f"modes[{k}].branching_ratio_uncertainty", hm.get("branching_ratio_uncertainty"), wm["dbr"])
            exact(f"modes[{k}].q_eV", hm.get("q_eV"), wm["q"])
            exact(f"modes[{k}].daughter", hm.get("daughter"), wm["daughter"])
            exact(f"modes[{k}].daughter_za", hm.get("daughter_za"), wm["daughter_za"])
            exact(f"modes[{k}].daughter_liso", hm.get("daughter_liso"), wm["daughter_liso"])
        gm = have.get("gamma", {})
        exact("gamma.discrete_lines", gm.get("discrete_lines"), want["gamma_lines"])
        g_have, g_want = gm.get("total_energy_eV_per_decay"), want["gamma_energy"]
        if g_have is None or rel_diff(g_have, g_want) > TOL:
            f.add("gamma.total_energy_eV_per_decay", inspect=g_have, independent=g_want,
                  relative_difference=None if g_have is None else rel_diff(g_have, g_want))
        sp_have = have.get("spectra", [])
        if len(sp_have) != len(want["spectra"]):
            f.add("spectrum count", inspect=len(sp_have), independent=len(want["spectra"]))
        for k, (hs, ws) in enumerate(zip(sp_have, want["spectra"])):
            for field in ("styp", "discrete_lines", "has_continuum", "average_energy_eV"):
                exact(f"spectra[{k}].{field}", hs.get(field), ws[field])
        entry.update({
            "half_life_s": want["half_life_s"], "modes": [
                {"rtyp": m["rtyp"], "branching_ratio": m["br"], "daughter": m["daughter"]} for m in want["modes"]],
            "mean_energy_eV": {k: want[k] for k in ("beta", "gamma", "alpha")},
            "gamma_lines": want["gamma_lines"], "gamma_energy_eV_per_decay": want["gamma_energy"],
            "gamma_energy_inspect": g_have,
            "gamma_energy_relative_difference": None if g_have is None else rel_diff(g_have, g_want),
            "mismatches": f.record()})
    out["pass"] = bool(ok and all(e["mismatches"] and e["mismatches"]["count"] == 0
                                  for e in out["per_nuclide"].values()))
    return out


# ---------------------------------------------------------------- G3


def read_exact(fp, n):
    chunks = []
    while n > 0:
        b = fp.read(n)
        if not b:
            raise GateError("sig.npy ended early")
        chunks.append(b)
        n -= len(b)
    return b"".join(chunks)


def npz_rows_bounds(npz_path):
    with np.load(npz_path) as z:          # lazy: only `rows` and `bounds` are decoded here
        return np.array(z["rows"]), np.array(z["bounds"])


def npz_sig_rows(npz_path, wanted, ngroups_expected, nrows_expected):
    """sig[row, :] for the sorted row indices `wanted`, streamed from the NPZ member (sig is ~1 GB for the full
    library, so it is not decoded whole). Returns an array (len(wanted), G)."""
    with zipfile.ZipFile(npz_path) as zf:
        with zf.open("sig.npy") as fp:
            version = np.lib.format.read_magic(fp)
            reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                      else np.lib.format.read_array_header_2_0)
            shape, fortran, dtype = reader(fp)
            if fortran or dtype != np.dtype("<f8") or shape != (nrows_expected, ngroups_expected):
                raise GateError(f"sig.npy is {shape} {dtype} fortran={fortran}; expected "
                                f"({nrows_expected}, {ngroups_expected}) <f8 C order")
            row_bytes = ngroups_expected * 8
            base = fp.tell()
            out = np.empty((len(wanted), ngroups_expected), dtype="<f8")
            k = 0
            while k < len(wanted):
                run = 1
                while k + run < len(wanted) and wanted[k + run] == wanted[k] + run:
                    run += 1
                fp.seek(base + int(wanted[k]) * row_bytes)
                block = np.frombuffer(read_exact(fp, run * row_bytes), dtype="<f8")
                out[k:k + run] = block.reshape(run, ngroups_expected)
                k += run
    return out


def ascending_flux(spectrum):
    """The solver's group flux in ascending energy order, scaled to `total` when given (Spectrum::ascending_flux)."""
    f = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending", False):
        f.reverse()
    total = spectrum.get("total")
    if total is not None:
        s = sum(f)
        if s > 0.0:
            k = total / s
            f = [v * k for v in f]
    return np.array(f, dtype="<f8")


def collapse(sig_row, phi):
    """sum(sigma_g * phi_g) / sum(phi_g); exact-sum accumulation (math.fsum), zero when the flux sums to zero."""
    den = math.fsum(phi.tolist())
    if not den > 0.0:
        return 0.0
    return math.fsum((sig_row * phi).tolist()) / den


def gate_g3(binary, library, spec_path, spec, work, evidence):
    index_path = str(library)[:-4] + "_index.json" if str(library).endswith(".npz") else str(library) + "_index.json"
    index = json.loads(Path(index_path).read_text())
    targets = [(t["za"], t["liso"]) for t in index["targets"]]
    fe56 = targets.index((26056, 0))
    if targets.count((26056, 0)) != 1:
        raise GateError("Fe56 appears more than once in the library index")
    rows, bounds = npz_rows_bounds(library)
    ngroups = len(bounds) - 1
    out = {"library_groups": ngroups, "library_rows": int(rows.shape[0]), "target_index": fe56,
           "index_groups": index.get("groups"), "spectrum_structure": spec["spectrum"].get("structure")}
    if not np.all(np.diff(bounds) > 0.0):
        raise GateError("library bounds are not strictly ascending; the ascending-order convention fails")
    sel = np.nonzero(rows[:, 0] == fe56)[0]
    out["fe56_rows"] = int(sel.size)
    if sel.size == 0:
        raise GateError("no Fe56 rows in the library")
    sig = npz_sig_rows(library, sel.tolist(), ngroups, int(rows.shape[0]))

    spectrum = spec["spectrum"]
    if len(spectrum["flux_per_group"]) != ngroups:
        raise GateError(f"spectrum has {len(spectrum['flux_per_group'])} groups, library has {ngroups}")
    phi = ascending_flux(spectrum)
    total_flux = math.fsum(phi.tolist())
    peak = int(np.argmax(phi))
    out["convention"] = ("library groups ascending in energy; spectrum reversed when `descending`, then scaled to "
                         "`total`; collapse = sum(sig_g*phi_g)/sum(phi_g)")
    out["spectrum_descending"] = bool(spectrum.get("descending", False))
    out["total_flux_cm2_s"] = total_flux
    out["peak_flux_group"] = {"ascending_index": peak, "low_eV": float(bounds[peak]),
                              "high_eV": float(bounds[peak + 1]), "flux": float(phi[peak])}

    groups = inspect_json(binary, ["groups", spectrum.get("structure", "fispact-709")], work, "g3_groups")
    gb = groups.get("boundaries_eV", [])
    out["structure_bounds_bitwise_equal_library"] = (len(gb) == len(bounds) and
                                                     all(float(a) == float(b) for a, b in zip(gb, bounds.tolist())))
    out["structure_matches_index"] = index.get("groups") == spectrum.get("structure")

    report = inspect_json(binary, ["xs", str(library), "--target", "Fe56", "--spectrum", str(spec_path)],
                          work, "g3_xs")
    f = Findings()
    if report.get("schema") != "actinv-inspect-xs-1":
        f.add("schema", inspect=report.get("schema"))
    reactions = report.get("reactions", [])
    if len(reactions) != sel.size:
        f.add("reaction count", inspect=len(reactions), independent=int(sel.size))
    rb = report.get("group_bounds_eV", [])
    if len(rb) != len(bounds) or any(float(a) != float(b) for a, b in zip(rb, bounds.tolist())):
        f.add("group_bounds_eV differ from the NPZ bounds")
    sp = report.get("spectrum", {})
    if sp.get("total_flux_cm2_s") is None or rel_diff(sp["total_flux_cm2_s"], total_flux) > TOL:
        f.add("spectrum.total_flux_cm2_s", inspect=sp.get("total_flux_cm2_s"), independent=total_flux)
    worst = {"group_xs": 0.0, "one_group_xs": 0.0, "rate_vs_independent_xs": 0.0, "rate_vs_inspect_xs": 0.0}
    table = []
    for k, (rx, row_index) in enumerate(zip(reactions, sel.tolist())):
        meta = rows[row_index]
        for field, expect in (("target_index", meta[0]), ("mt", meta[1]), ("product_za", meta[2]),
                              ("product_lfs", meta[3]), ("lmf", meta[4])):
            if rx.get(field) != int(expect):
                f.add(f"reactions[{k}].{field}", inspect=rx.get(field), independent=int(expect))
        row = sig[k]
        groupwise = rx.get("cross_section_b", [])
        if len(groupwise) != ngroups:
            f.add(f"reactions[{k}].cross_section_b length", inspect=len(groupwise), independent=ngroups)
        else:
            d = max(rel_diff(a, b) for a, b in zip(groupwise, row.tolist()))
            worst["group_xs"] = max(worst["group_xs"], d)
            if d > TOL:
                f.add(f"reactions[{k}] group cross sections", mt=int(meta[1]), worst_relative_difference=d)
        if rx.get("nonzero_groups") != int(np.count_nonzero(row > 0.0)):
            f.add(f"reactions[{k}].nonzero_groups", inspect=rx.get("nonzero_groups"),
                  independent=int(np.count_nonzero(row > 0.0)))
        if rel_diff(rx.get("max_cross_section_b"), float(row.max())) > TOL:
            f.add(f"reactions[{k}].max_cross_section_b", inspect=rx.get("max_cross_section_b"),
                  independent=float(row.max()))
        xs_mine = collapse(row, phi)
        rate_mine = xs_mine * 1e-24 * total_flux
        xs_got = rx.get("one_group_xs_b")
        rate_got = rx.get("reaction_rate_per_target_atom_per_s")
        d_xs = math.inf if xs_got is None else rel_diff(xs_got, xs_mine)
        d_rate_i = math.inf if rate_got is None or xs_got is None else rel_diff(rate_got, xs_got * 1e-24 * total_flux)
        d_rate_m = math.inf if rate_got is None else rel_diff(rate_got, rate_mine)
        worst["one_group_xs"] = max(worst["one_group_xs"], d_xs)
        worst["rate_vs_inspect_xs"] = max(worst["rate_vs_inspect_xs"], d_rate_i)
        worst["rate_vs_independent_xs"] = max(worst["rate_vs_independent_xs"], d_rate_m)
        if d_xs > TOL:
            f.add(f"reactions[{k}].one_group_xs_b", mt=int(meta[1]), inspect=xs_got, independent=xs_mine,
                  relative_difference=d_xs)
        if d_rate_i > TOL or d_rate_m > TOL:
            f.add(f"reactions[{k}].reaction_rate", mt=int(meta[1]), inspect=rate_got, independent=rate_mine,
                  relative_difference=max(d_rate_i, d_rate_m))
        table.append({"mt": int(meta[1]), "product_za": int(meta[2]), "product_lfs": int(meta[3]),
                      "one_group_xs_b": xs_mine, "inspect_one_group_xs_b": xs_got,
                      "rate_per_atom_per_s": rate_mine, "inspect_rate_per_atom_per_s": rate_got})
    # --mt filter must return exactly the matching rows with the same values.
    mt_report = inspect_json(binary, ["xs", str(library), "--target", "Fe56", "--mt", "103", "--spectrum",
                                      str(spec_path)], work, "g3_xs_mt103")
    by_mt = [r for r in reactions if r.get("mt") == 103]
    got_mt = mt_report.get("reactions", [])
    if len(got_mt) != len(by_mt):
        f.add("--mt 103 reaction count", inspect=len(got_mt), independent=len(by_mt))
    for a, b in zip(got_mt, by_mt):
        for field in ("product_za", "product_lfs", "one_group_xs_b", "reaction_rate_per_target_atom_per_s"):
            if a.get(field) != b.get(field):
                f.add(f"--mt 103 {field}", mt_filtered=a.get(field), unfiltered=b.get(field))
    # The library also carries an MT=103 row with no residual (ZAP=-1) that repeats the same cross section; the
    # solver's (n,p) channel is the row with a residual product, so only those rows are summed for G4.
    np_rows = [t for t in table if t["mt"] == 103 and t["product_za"] >= 1000]
    evidence["fe56_np"] = {"rows": np_rows,
                           "rate_per_target_atom_per_s": math.fsum(t["rate_per_atom_per_s"] for t in np_rows),
                           "one_group_xs_b_sum": math.fsum(t["one_group_xs_b"] for t in np_rows)}
    out.update({"reactions": len(reactions), "worst_relative_difference": worst, "mismatches": f.record(),
                "table": table})
    out["pass"] = bool(f.count == 0 and out["structure_bounds_bitwise_equal_library"]
                       and out["structure_matches_index"] and len(np_rows) > 0)
    if not np_rows:
        out["note"] = "Fe56 has no MT=103 row with a residual product; G4 has nothing to compare"
    return out


# ---------------------------------------------------------------- G4


RATE_KEY = re.compile(r"(one_?group|collaps|cross_?section|sigma|reaction_?rate|reaction_?rates|rxn|(^|_)xs($|_))",
                      re.IGNORECASE)


def walk_numeric(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_numeric(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_numeric(v, path + (str(i),))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj


def walk_keys(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + (str(k),)
            yield from walk_keys(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_keys(v, path + (str(i),))


def doc_hits():
    pattern = re.compile(r"reaction[ -]rate|one-group|one_group|collapsed cross|collapsed one", re.IGNORECASE)
    hits = []
    for rel in ("docs/guide/results.md", "docs/LEDGER.md"):
        p = ROOT / rel
        if not p.exists():
            hits.append({"file": rel, "missing": True})
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                hits.append({"file": rel, "line": n, "text": line.strip()[:160]})
    return hits


def gate_g4(binary, spec_path, work, evidence, reuse, result_path):
    np_info = evidence.get("fe56_np")
    if np_info is None:
        raise GateError("G3 produced no Fe56 (n,p) rate to compare")
    out_path = Path(result_path) if result_path else work / "fns_fe_5min.result.json"
    if not (reuse and out_path.exists()) and not result_path:
        code, _, err = run_cmd([binary, "run", spec_path, out_path], RUN_TIMEOUT_S, cwd=ROOT,
                               log=work / "g4_run.log")
        if code != 0:
            raise GateError(f"solver exited {code}: {err.decode(errors='replace').strip()[:300]}")
    result = json.loads(out_path.read_text())
    out = {"result": str(out_path), "result_sha256": sha256_file(out_path), "docs_hits": doc_hits(),
           "top_level_keys": sorted(result.keys()) if isinstance(result, dict) else None}
    candidates = []
    for path, value in walk_numeric(result):
        if any(RATE_KEY.search(part) for part in path):
            candidates.append({"path": "/".join(path), "value": value})
    # Also: any key path naming the reaction itself (Fe56, MT 103, "(n,p)").
    named = [{"path": "/".join(p)} for p in walk_keys(result)
             if re.search(r"fe56|\(n,p\)|mt_?103", "/".join(p), re.IGNORECASE)
             and re.search(r"rate|xs|cross|sigma|collaps", "/".join(p), re.IGNORECASE)]
    out["candidate_numeric_fields"] = candidates[:40]
    out["candidate_count"] = len(candidates)
    out["fields_naming_fe56_np"] = named[:40]
    rate, xs = np_info["rate_per_target_atom_per_s"], np_info["one_group_xs_b_sum"]
    out["inspect_fe56_np"] = {"rate_per_target_atom_per_s": rate, "one_group_xs_b": xs}
    matches = [c for c in candidates
               if (rate != 0.0 and rel_diff(c["value"], rate) <= TOL) or (xs != 0.0 and rel_diff(c["value"], xs) <= TOL)]
    if matches:
        out["status"] = "compared"
        out["matching_fields"] = matches[:10]
        out["pass"] = True
    elif named:
        out["status"] = "fail"
        out["reason"] = "result has fields naming the Fe56 (n,p) rate or cross section, none equal the inspect value"
        out["pass"] = False
    else:
        out["status"] = "not_applicable"
        out["reason"] = ("the solver result and its ledger carry no per-reaction rate or collapsed cross section for "
                         "Fe56 (n,p): the result reports inventories, activity, heat, photons, pathways (atoms and "
                         "fractions) and chain/defect ledgers only, and docs/guide/results.md and docs/LEDGER.md "
                         "document no such field; the matrix reaction rates are internal to the solve")
        out["pass"] = True
    return out


# ---------------------------------------------------------------- G5


def parse_yields(path):
    sec = sections(path, {(8, 454), (8, 459), (1, 451)})
    by_mt = {}
    for (mat, mf, mt), lines in sec.items():
        by_mt.setdefault((mf, mt), []).append(lines)
    for key, found in by_mt.items():
        if len(found) != 1:
            raise GateError(f"MF={key[0]}/MT={key[1]}: expected one section, found {len(found)}")
    if (8, 454) not in by_mt:
        raise GateError("no MF=8/MT=454 section")
    liso = Records(by_mt[(1, 451)][0][1:2]).cont()[3]      # second record of MF=1/MT=451: (.., .., LIS, LISO, .., ..)
    parsed = {}
    for mt in (454, 459):
        if (8, mt) not in by_mt:
            continue
        r = Records(by_mt[(8, mt)][0])
        za_f, awr, le, _, _, _ = r.cont()
        tables = []
        for _ in range(le):
            head, v = r.lst()
            nfp = head[5]
            if head[4] != 4 * nfp:
                raise GateError(f"MT={mt}: NPL {head[4]} != 4*NFP {4 * nfp}")
            products = {}
            for k in range(nfp):
                za_p, fps, y, dy = v[4 * k:4 * k + 4]
                key = (int(round(za_p)), int(round(fps)))
                if key in products:
                    raise GateError(f"MT={mt}: duplicate product {key} at E={head[0]}")
                products[key] = (y, dy)
            tables.append({"energy": head[0], "products": products})
        if not r.done():
            raise GateError(f"MT={mt}: unconsumed records")
        tables.sort(key=lambda t: t["energy"])
        parsed[mt] = {"za": int(round(za_f)), "awr": awr, "tables": tables}
    return {"za": parsed[454]["za"], "liso": liso, "awr": parsed[454]["awr"], "independent": parsed[454]["tables"],
            "cumulative": parsed[459]["tables"] if 459 in parsed else []}


def interpolate(tables, e):
    """The rule documented in crates/actinv-data/src/fission.rs `effective`: clamp to the first/last tabulated energy
    (weight 0); otherwise linear in incident energy between the bracketing tables, w=(E-lo)/(hi-lo),
    Y = lo*(1-w)+hi*w over the union of products (absent = 0), uncertainty = hypot((1-w)*u_lo, w*u_hi)."""
    energies = [t["energy"] for t in tables]
    if e <= energies[0]:
        lo = hi = tables[0]
        w, clamped = 0.0, e < energies[0]
    elif e >= energies[-1]:
        lo = hi = tables[-1]
        w, clamped = 0.0, e > energies[-1]
    else:
        u = bisect.bisect_right(energies, e)
        lo, hi = tables[u - 1], tables[u]
        w, clamped = (e - lo["energy"]) / (hi["energy"] - lo["energy"]), False
    keys = sorted(set(lo["products"]) | set(hi["products"]))
    values, uncertainties = {}, {}
    for k in keys:
        yl, ul = lo["products"].get(k, (0.0, 0.0))
        yh, uh = hi["products"].get(k, (0.0, 0.0))
        values[k] = yl * (1.0 - w) + yh * w
        uncertainties[k] = math.hypot((1.0 - w) * ul, w * uh)
    return {"lower": lo["energy"], "upper": hi["energy"], "weight": w, "clamped": clamped,
            "values": values, "uncertainties": uncertainties, "sum": math.fsum(values.values())}


def compare_products(got_products, want, label, f):
    """Every product matches: same key set, yield and uncertainty within 1e-12 relative."""
    got = {(p["za"], p["liso"]): (p["yield"], p["uncertainty"]) for p in got_products}
    if len(got) != len(got_products):
        f.add(f"{label}: duplicate products in inspect output")
    if set(got) != set(want["values"]):
        f.add(f"{label}: product set differs", only_inspect=sorted(set(got) - set(want["values"]))[:5],
              only_independent=sorted(set(want["values"]) - set(got))[:5])
    worst = 0.0
    for k in sorted(set(got) & set(want["values"])):
        dy = rel_diff(got[k][0], want["values"][k])
        du = rel_diff(got[k][1], want["uncertainties"][k])
        worst = max(worst, dy, du)
        if dy > TOL or du > TOL:
            f.add(f"{label}: product {k}", inspect=got[k], independent=(want["values"][k], want["uncertainties"][k]))
    return worst, len(got)


def gate_g5(binary, yields_path, work):
    ev = parse_yields(yields_path)
    out = {"parent": {"za": ev["za"], "liso": ev["liso"], "awr": ev["awr"]},
           "tabulated_energies_eV": [t["energy"] for t in ev["independent"]],
           "cumulative_available": bool(ev["cumulative"]), "cases": []}
    if not ev["cumulative"]:
        raise GateError("the yields file has no MT=459 section; --cumulative cannot be checked")
    energies = out["tabulated_energies_eV"]
    # 0.0253 eV is the first tabulated point of the ENDF/B-VIII.0 files; fall back to the first point otherwise.
    thermal = 0.0253 if 0.0253 in energies else energies[0]
    between = [float(f"{lo + 0.37 * (hi - lo):.6g}") for lo, hi in zip(energies, energies[1:])]
    exact_mid = energies[1] if len(energies) > 2 else None
    plan = [(thermal, False), (thermal, True)]
    plan += [(e, False) for e in between] + [(e, True) for e in between]
    if exact_mid is not None:
        plan += [(exact_mid, False), (exact_mid, True)]
    plan += [(0.0, False), (energies[-1] * 2.0, False), (energies[-1] * 2.0, True)]
    all_ok = True
    for n, (e, cumulative) in enumerate(plan):
        tables = ev["cumulative"] if cumulative else ev["independent"]
        want = interpolate(tables, e)
        args = ["yields", yields_path, "--parent", "U235", "--energy", repr(e)] + (["--cumulative"] if cumulative else [])
        label = f"{'cumulative' if cumulative else 'independent'} E={e!r}"
        report = inspect_json(binary, args, work, f"g5_{n}")
        f = Findings()
        if report.get("schema") != "actinv-inspect-yields-1":
            f.add("schema", inspect=report.get("schema"))
        if report.get("kind") != ("cumulative" if cumulative else "independent"):
            f.add("kind", inspect=report.get("kind"))
        parents = report.get("parents", [])
        worst, nprod = 0.0, 0
        if len(parents) != 1:
            f.add("parent count", inspect=len(parents))
        else:
            p = parents[0]
            for field, expect in (("parent", "U235"), ("parent_za", ev["za"]), ("parent_liso", ev["liso"]),
                                  ("awr", ev["awr"]), ("lower_energy_eV", want["lower"]),
                                  ("upper_energy_eV", want["upper"]), ("clamped", want["clamped"]),
                                  ("requested_energy_eV", e)):
                if p.get(field) != expect:
                    f.add(field, inspect=p.get(field), independent=expect)
            if rel_diff(p.get("upper_weight"), want["weight"]) > TOL:
                f.add("upper_weight", inspect=p.get("upper_weight"), independent=want["weight"])
            if rel_diff(p.get("sum"), want["sum"]) > TOL:
                f.add("sum", inspect=p.get("sum"), independent=want["sum"])
            if p.get("product_count") != len(want["values"]):
                f.add("product_count", inspect=p.get("product_count"), independent=len(want["values"]))
            worst, nprod = compare_products(p.get("products", []), want, label, f)
        case = {"kind": "cumulative" if cumulative else "independent", "energy_eV": e, "lower_eV": want["lower"],
                "upper_eV": want["upper"], "upper_weight": want["weight"], "clamped": want["clamped"],
                "products": nprod, "sum": want["sum"], "worst_relative_difference": worst,
                "mismatches": f.record(), "pass": f.count == 0}
        out["cases"].append(case)
        all_ok = all_ok and case["pass"]
    # No --energy: the tabulated tables themselves, every product at every incident energy.
    for cumulative in (False, True):
        tables = ev["cumulative"] if cumulative else ev["independent"]
        report = inspect_json(binary, ["yields", yields_path, "--parent", "U235"] + (["--cumulative"] if cumulative else []),
                              work, f"g5_tables_{int(cumulative)}")
        f = Findings()
        listed = report.get("parents", [{}])[0].get("energies", [])
        if len(listed) != len(tables):
            f.add("tabulated energy count", inspect=len(listed), independent=len(tables))
        worst = 0.0
        for got, t in zip(listed, tables):
            if got.get("energy_eV") != t["energy"]:
                f.add("energy_eV", inspect=got.get("energy_eV"), independent=t["energy"])
            want = {"values": {k: v[0] for k, v in t["products"].items()},
                    "uncertainties": {k: v[1] for k, v in t["products"].items()}}
            w, _ = compare_products(got.get("products", []), want, f"table E={t['energy']!r}", f)
            worst = max(worst, w)
            if rel_diff(got.get("sum"), math.fsum(v[0] for v in t["products"].values())) > TOL:
                f.add("table sum", energy=t["energy"], inspect=got.get("sum"))
        case = {"kind": ("cumulative" if cumulative else "independent") + "_tabulated", "energies": len(listed),
                "worst_relative_difference": worst, "mismatches": f.record(), "pass": f.count == 0}
        out["cases"].append(case)
        all_ok = all_ok and case["pass"]
    out["interpolation_rule"] = ("fission.rs FissionYields::effective: clamp outside the tabulated range; linear in "
                                 "energy between bracketing tables, union of products, absent = 0, "
                                 "uncertainty = hypot((1-w)*u_lo, w*u_hi)")
    out["pass"] = all_ok
    return out


# ---------------------------------------------------------------- driver


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True, help="release actinv binary of the P131 implementation commit")
    ap.add_argument("--decay", help="ENDF decay file (default: spec decay.primary)")
    ap.add_argument("--yields", default=DEFAULT_YIELDS, help="ENDF/B-VIII.0 nfpy U-235 file")
    ap.add_argument("--library", help="activation library NPZ (default: spec library.path)")
    ap.add_argument("--spec", default=DEFAULT_SPEC)
    ap.add_argument("--work", default="target/p131")
    ap.add_argument("--out", default="results/p131_inspect.json")
    ap.add_argument("--result", help="existing solver result JSON for G4 (skips running the solver)")
    ap.add_argument("--reuse", action="store_true", help="reuse an existing solver result in --work for G4")
    args = ap.parse_args(argv)

    binary = Path(args.candidate).resolve()
    spec_path = Path(args.spec)
    spec_path = (spec_path if spec_path.is_absolute() else ROOT / spec_path).resolve()
    yields_path = Path(os.path.expanduser(args.yields)).resolve()
    try:
        spec = json.loads(spec_path.read_text())
        decay_ref = args.decay or spec["decay"]["primary"]
        library_ref = args.library or spec["library"]["path"]
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot read the spec {spec_path}: {error}", file=sys.stderr)
        return 2
    decay_path = Path(decay_ref) if Path(decay_ref).is_absolute() else ROOT / decay_ref
    library = Path(library_ref) if Path(library_ref).is_absolute() else ROOT / library_ref
    for label, p in (("candidate binary", binary), ("decay file", decay_path), ("yields file", yields_path),
                     ("library", library), ("library index", Path(str(library)[:-4] + "_index.json"))):
        if not Path(p).is_file():
            print(f"missing {label}: {p}", file=sys.stderr)
            return 2

    work = (ROOT / args.work).resolve() if not Path(args.work).is_absolute() else Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    record = {"protocol": "ACTINV-P131",
              "candidate": str(binary), "candidate_sha256": sha256_file(binary),
              "checker_sha256": sha256_file(Path(__file__).resolve()),
              "inputs": {"spec": {"path": str(spec_path), "sha256": sha256_file(spec_path)},
                         "decay": {"path": str(decay_path), "sha256": sha256_file(decay_path)},
                         "yields": {"path": str(yields_path), "sha256": sha256_file(yields_path)},
                         "library": {"path": str(library), "sha256": sha256_file(library)},
                         "library_index": {"path": str(library)[:-4] + "_index.json",
                                           "sha256": sha256_file(Path(str(library)[:-4] + "_index.json"))}},
              "gates": {}}
    pinned = (spec.get("library") or {}).get("sha256")
    record["inputs"]["library"]["spec_sha256"] = pinned
    precondition = pinned is None or pinned == record["inputs"]["library"]["sha256"]
    record["inputs"]["library"]["matches_spec"] = precondition
    pin_decay = (spec.get("decay") or {}).get("primary_sha256")
    if pin_decay is not None:
        record["inputs"]["decay"]["spec_sha256"] = pin_decay
        precondition = precondition and pin_decay == record["inputs"]["decay"]["sha256"]

    evidence = {}
    gates = [("G2", lambda: gate_g2(str(binary), str(decay_path), work)),
             ("G3", lambda: gate_g3(str(binary), library, spec_path, spec, work, evidence)),
             ("G4", lambda: gate_g4(str(binary), spec_path, work, evidence, args.reuse, args.result)),
             ("G5", lambda: gate_g5(str(binary), str(yields_path), work))]
    for name, fn in gates:
        try:
            record["gates"][name] = fn()
        except (GateError, OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile) as error:
            record["gates"][name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    g4 = record["gates"]["G4"]
    verdicts = {name: ("PASS" if g.get("pass") else "FAIL") for name, g in record["gates"].items()}
    if g4.get("status") == "not_applicable" and g4.get("pass"):
        verdicts["G4"] = "NOT APPLICABLE"
    record["verdicts"] = verdicts
    record["precondition_inputs_match_spec"] = precondition
    record["pass"] = bool(precondition and all(g.get("pass") for g in record["gates"].values()))
    record["verdict"] = "P131-PASS" if record["pass"] else "P131-FAIL"
    out = Path(args.out)
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    for name, v in verdicts.items():
        extra = f" ({record['gates'][name]['error']})" if "error" in record["gates"][name] else ""
        print(f"P131 {name}: {v}{extra}")
    if not precondition:
        print("P131 precondition: an input does not match the SHA-256 pinned in the spec")
    print(f"P131: {record['verdict']} ({out})")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
