#!/usr/bin/env python3
"""P156 checker (protocols/ACTINV-P156_PROTOCOL.md): dpa without setup, a shipped damage table (data catalog 1.2.0).

Independence: written from the protocol, the user documentation (docs/guide/{specification,cli,data,fispact-migration}.md,
docs/DATA.md), the catalog JSON files, results/p154_*.json and the prior independent checker controls/check_p154.py
(with controls/p154_njoy.py and P154 Amendments A-C). No ACTINV source file was read. G1 and G8 are external.

Reused from check_p154 by import (never copied): sh, sha256, rel, run_spec, fold, spectrum_ascending, parse_duration,
vendored_boundaries, check_run_with_bounds (G6 run reproduction), gate_g2 (demo / FNS / P23 byte comparison),
gates_g3_g4_g5 (the NJOY comparisons with Amendment C's preconditions and references), build_args, njoy_capture_treatment,
njoy_continuum_recoil, NORDLUND_TABLE_1, FE_ABUNDANCE, TARGETS / BUILD_ED (P154 recorded build parameters). What P154
hard-codes (its four targets and their displacement energies) is parameterized by temporarily rebinding those module
globals around the call (`p154_targets`), never by editing check_p154.py.

Amendment D (w_table_d1, level_w_tables_d2, apply_d3, the G7 notice supersession) was added by the coordinating
maintainer after development run 3, who had read the candidate's Reich-Moore code while diagnosing Al-27. Those
changes concern only the ENDF / NJOY references and the gate bookkeeping, not anything the candidate computes.

  G0  This protocol's and Amendments A, B, C and D's SHA-256 are in protocols/protocol_hash.txt.
  G1  External (fmt, clippy -D warnings, workspace tests): recorded; `--g1-log PATH` records the log's digest and tail.
  G2  Existing behaviour. examples/damage_demo.json and examples/fns_fe_5min.json run with reference and candidate are
      byte-identical apart from `ms`; `build-damage` without `--method` is byte-identical on the P23 corpus (both are
      check_p154.gate_g2); the four P154 targets (Fe-56, Cr-52, Ni-58, W-184) built with P154's recorded arguments
      (explicit --displacement-energy at P154's values, the capture / continuum modes recorded in
      results/p154_damage.json) give byte-identical tables (and stdout) from reference and candidate. The reference binary
      predates the new defaults, so the energies are always passed explicitly.
  G3  Energies. A candidate kinematic build with no --displacement-energy of a corpus of regular-file copies (Fe-56, W-184,
      Be-9, Li-7, Cu-63 and the smallest TENDL file of every other element of the standard table) records exactly
      the NJOY HEATR table (Be 31, C 31, Mg 25, Al 27, Si 25, Ca 40, Ti V Cr Mn Fe Co Ni Cu 40, Zr 40, Nb 40, Mo 60,
      Ag 60, Ta 90, W 90, Au 30, Pb 25; 25 otherwise) for each element present and nothing else. W-184 at W=90 (the
      default) and W=70 (Amendment A1): displacement_arc_barn within 1e-3 relative and displacement_nrt_barn more than 1e-2 apart in
      every group above 1e-6 of the maximum; largest arc and smallest NRT difference reported. Fe-56 at Fe=40: the arc column
      equals the reference binary's, and the candidate's no-option build of Fe-56 is identical to it. Cu-63 at its default
      (40) and at Cu=33 (Nordlund's reference): the same numerical comparison as W-184 (item 2).
  G4  Wider accuracy. TENDL-2025 V-51, Ti-48, Cu-63, Ta-181, Mo-98, Nb-93, Mn-55, Co-59, Si-28, Al-27, Be-9, Li-7: P154's
      G3 (elastic, 2 %), G4 (inelastic and disappearance, 5 %, Amendment C precondition set and references, with P156
      Amendment D1-D3: LTT=3 step, HEATR's 64-point level rule, uncovered-vacuous comparisons reported) and G5 (NJOY
      MT=444 within computed plus uncovered bound) against NJOY at E_d equal to the candidate's standard energy of the
      element, with the NJOY-matching capture / continuum modes chosen per target from the file as check_p154 does. A
      target is a case; a case whose evaluation lacks a data form P154 covers is reported with its uncovered share (FNS
      folded uncovered upper bound over computed plus upper bound) and still has to satisfy the criteria; no target is
      dropped or exempted. The extra capture / continuum mode combinations of P154 are information only and are skipped
      unless --mode-information.
  G5  Shipped table. The selection is derived here: every TENDL file n-<El><AAA>[m|n].tendl of --tendl-dir whose nuclide
      has an MF=8 MT=457 record (HEAD: ZA, AWR, LIS, LISO, NST, NSP, LISO in columns 34-44, NST in 45-55; T1/2 the first
      field of the next line) in the primary decay file (ENDF/B-VIII.0), else in the fallback (JEFF-3.3), with NST = 1
      or T1/2 >= 3.15576e7 s. Files with no record in either are excluded and listed. The table (--table): format
      actinv-damage-table-2; `files` equals the selection (names and SHA-256, the latter recomputed from --tendl-dir);
      every selected nuclide is a row of `targets` or is named with a non-empty reason in `uncovered`; every row finite
      and nonnegative; damage_energy_barn_eV equals elastic + inelastic + disappearance in every group within 1e-12
      relative; the recorded displacement energies are the standard table. Amendment B: the build adds `--continue-on-error true`;
      a selected target is satisfied by a row or by a `failed` entry ({file, sha256, error}) naming its file; failed and
      clipped (`channels.zero_k_clipped`) targets are counted with natural abundance; a failed stable nuclide of Fe, Cr, Ni,
      W, V, Ti, Ta, Mo, Nb, Mn, Co, Cu, Al, Si, Be or Li fails G5; G4 reports clip records of its twelve targets (information). A seeded (random.Random(156).sample of the
      sorted selection) 16-file subset is copied (regular files) and rebuilt with the frozen item-6 command; each
      rebuilt row's canonical JSON equals the shipped row's. Build time, peak memory and size are reported (--build-log:
      GNU time -v output; the rebuild's own wall time and child peak RSS are always recorded).
  G6  No setup. `actinv new --data-dir DIR` output runs; its per-step damage blocks and ledger damage equal those of the
      same spec with damage.table path and sha256 explicit and every energy written out, once the energy-source field
      (ledger.damage.displacement_energy_source, element -> spec | table) is removed; that field is `table` for every element
      in the first and `spec` in the second; both runs have a damage block on every step (`new` leaves options.outputs unset). FNS Fe (examples/fns_fe_5min.json plus damage, gas) on the
      shipped table: dpa, dpa_nrt, dpa_arc and the rates / bounds reproduced from the table rows (atom-weighted over the
      covered Fe isotopes), the flux multipliers, durations and spectrum within 1e-12 relative (check_run_with_bounds);
      covered_atom_fraction present at every step and equal to the abundance of the covered isotopes; a spec giving
      Fe=41 is refused (nonzero exit, no output file); an FNS Fe deck with ALLDISPEN at the table's energy imports
      without --damage with the displacement keyword `mapped` and a damage section, and ALLDISPEN at another energy is
      `unsupported` (as for --damage).
  G7  Catalog. `actinv data manifest` is valid JSON equal to the repository's catalog 1.2.0 and `data list` runs;
      catalog_version 1.2.0; every v1.1.0 artifact's id, path, bytes, sha256 and source unchanged; one artifact of role
      `damage-table`, id tendl-2025-neutron-damage, CC-BY-4.0, hosted on release data-v1.2.0, listed by the four neutron
      bundles and no other; v1.2.0 notice file present. With --staged DIR: SHA256SUMS and SIZES match the files and the
      table asset matches the catalog. With --fetch-check: a fetch into a directory seeded with copies of the v1.1.0
      files downloads only the table (network log, else fetch output, else file identity: v1.1.0 files untouched and
      every other v1.2.0 artifact byte-equal to a seeded file). With --published: clean `data fetch`, `data verify` and
      the G6 FNS run from that fetch.
  G8  External (CI).

Open points of the protocol (recorded in the JSON):
  A1  Row group weights of the shipped Fe material are the abundance copy of check_p154 (FE_ABUNDANCE) over the Fe isotopes
      that have a table row; the material rate is the covered-atom-weighted mean (specification.md).
  A2  `uncovered` entries are located by the nuclide name (Fe56, Ta180m1) or the file name appearing in the entry.
  A3  The comparison of `actinv new` with the explicit spec covers steps[*].damage and ledger.damage; the certificate is
      recorded as information.
  A4  A selected nuclide whose file name suffix is m / n is isomer 1 / 2 (canonical name Ta180m1 / Ta180m2).

Usage: check_p156.py --reference REF --candidate CAND [--gates G2,G5] [--workdir target/p156-work]
           [--out results/p156_damage.json] [--data-dir actinv-data] [--table TABLE.json] [--build-log LOG]
           [--g1-log LOG] [--staged DIR] [--fetch-check] [--published] [--mode-information] [--reuse]
       check_p156.py --self-check      (offline unit checks: no binary, no NJOY, no network)
Run inside the required systemd cgroup. Gates run serially. Exit 0 when every gate that ran passed.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import random
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p154 as p154  # noqa: E402
import p154_njoy as njoy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P156_PROTOCOL.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
CATALOG_DIR = ROOT / "crates" / "actinv-cli" / "data"
CATALOG_OLD = CATALOG_DIR / "actinv-data-catalog-v1.1.0.json"
CATALOG_NEW = CATALOG_DIR / "actinv-data-catalog-v1.2.0.json"
P154_RESULT = ROOT / "results" / "p154_damage.json"
TENDL_DIR = Path("~/nuclear-data/tendl-2025-patched/files/n").expanduser()
FNS_DIR = Path("~/nuclear-data/conderc-fns/fns").expanduser()
LIBRARY_DIR = ROOT / "actinv-data" / "v1.1.0"   # activation / decay files of examples/fns_fe_5min.json

GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")
EXTERNAL = ("G1", "G8")
YEAR_S = 3.15576e7                    # one Julian year (protocol item 4)
SEED = 156
SUBSET = 16
TOL_SUM = 1e-12                       # damage_energy_barn_eV against the sum of its partials (G5)
TOL_RUN = 1e-12
ENERGY_SOURCE_KEY = "displacement_energy_source"   # ledger.damage map element -> "spec" | "table" (table-2 runs)
AMENDMENT_A = ROOT / "protocols" / "ACTINV-P156_AMENDMENT_A.md"
AMENDMENT_B = ROOT / "protocols" / "ACTINV-P156_AMENDMENT_B.md"
AMENDMENT_C = ROOT / "protocols" / "ACTINV-P156_AMENDMENT_C.md"
AMENDMENT_D = ROOT / "protocols" / "ACTINV-P156_AMENDMENT_D.md"
# Amendment C1: the build directory takes Pb-208 from the P10 working corpus (the shipped library index source)
PB208_FILE = "n-Pb208.tendl"
PB208_WORKING_SHA256 = "86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72"
PB208_WORKING = Path.home() / "nuclear-data" / "p43-work" / "cov-inputs" / PB208_FILE
TOL_ARC = 1e-3                        # Amendment A1: arc agreement across NRT energies (the build REFINE_TOLERANCE)
TOL_NRT = 1e-2                        # Amendment A1: NRT must differ by more than this
A1_FLOOR = 1e-6                       # groups above this fraction of the maximum
CATALOG_ID = "tendl-2025-neutron-damage"
CATALOG_ROLE = "damage-table"
NEUTRON_BUNDLES = ("tendl-2025-neutron", "tendl-2025-neutron-covariance", "tendl-2025-patched-neutron",
                   "tendl-2025-patched-neutron-covariance")
ITEM6_ARGS = ["--method", "kinematic", "--projectile", "neutron", "--groups", "fispact-709",
              "--continuum-recoil", "evaluated", "--continue-on-error", "true"]   # Amendment B2
G3_ARGS = ["--method", "kinematic", "--projectile", "neutron", "--groups", "fispact-709"]

# NJOY2016 HEATR default displacement energies when no `ed` is given (protocol item 1; heatr.f90 tag 2016.80 lines
# 328-363), eV; 25 eV for every other element.
STANDARD_ED = {"Be": 31.0, "C": 31.0, "Mg": 25.0, "Al": 27.0, "Si": 25.0, "Ca": 40.0, "Zr": 40.0, "Nb": 40.0,
               "Mo": 60.0, "Ag": 60.0, "Ta": 90.0, "W": 90.0, "Au": 30.0, "Pb": 25.0}
for _el in ("Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu"):
    STANDARD_ED[_el] = 40.0
DEFAULT_ED = 25.0

# G4 targets: file stem, element, SHA-256 of the distributed TENDL-2025 neutron file (sha256sum, 2026-10-27).
G4_TARGETS = {
    "V51": ("n-V051.tendl", "V", "33d5d1b46c8f3efe21e852d6741ec575d7a08464eb58b636eb37a4ebb9598823"),
    "Ti48": ("n-Ti048.tendl", "Ti", "020118c02ce142b87e72b393dd48e41641b42abaedac290572b45be5018b1b7b"),
    "Cu63": ("n-Cu063.tendl", "Cu", "2cc049892a78cff58777e4be1e07c86f58f9fb5ee0f0f4814e8ef9c939759dd7"),
    "Ta181": ("n-Ta181.tendl", "Ta", "c17851bc0cee49949145b4218291430ce372b0dfc05fb1096b6566efa6701608"),
    "Mo98": ("n-Mo098.tendl", "Mo", "0bf3978c9dd8743a90da008e4b8a5f5277f13fb0f3b0bb8ecef752f2a3ad12b8"),
    "Nb93": ("n-Nb093.tendl", "Nb", "d7d8d946bf634bb5edc3c92f8dce4e72f40fe5f26ffa1b6c17330d7764f7954c"),
    "Mn55": ("n-Mn055.tendl", "Mn", "364707f2d85df022559021802eaa583337018017cc964c6dd7e1a66fd0d861a4"),
    "Co59": ("n-Co059.tendl", "Co", "00b705eec124c460f6f85f0ce22aaa216a75b26190dfde7c1fdc5430a85358b5"),
    "Si28": ("n-Si028.tendl", "Si", "8bc9da61e97236efc62b565c4cfc66358bbfa9a1ac20e835351092e0759dd916"),
    "Al27": ("n-Al027.tendl", "Al", "61afaf01ac4fca5b5d43bfcedaffaa66b5f36dddb9c1c657b4b0a21ea63d2ffd"),
    "Be9": ("n-Be009.tendl", "Be", "1da8eb7872395b119ab6cab3a374838764fde831619cc679b5a396422fd293be"),
    "Li7": ("n-Li007.tendl", "Li", "1f77be22830b17005b85698f03b74833bc012e2228c4c81a9a0a463b73a8ffdf"),
}

# Natural abundances (atom percent), IUPAC CIAAW representative isotopic compositions (Meija et al., Pure Appl. Chem. 88,
# 293 (2016); Ta-180m from the same tables), for the stable isotopes of the Amendment B2 structural / breeder elements.
# Keys are canonical nuclide names; Ta180m1 is an isomer (not stable) and is listed for the report only.
NATURAL_ABUNDANCE_PERCENT = {
    "Li6": 7.59, "Li7": 92.41, "Be9": 100.0, "Al27": 100.0, "Si28": 92.223, "Si29": 4.685, "Si30": 3.092,
    "Ti46": 8.25, "Ti47": 7.44, "Ti48": 73.72, "Ti49": 5.41, "Ti50": 5.18, "V50": 0.250, "V51": 99.750,
    "Cr50": 4.345, "Cr52": 83.789, "Cr53": 9.501, "Cr54": 2.365, "Mn55": 100.0,
    "Fe54": 5.845, "Fe56": 91.754, "Fe57": 2.119, "Fe58": 0.282, "Co59": 100.0,
    "Ni58": 68.0769, "Ni60": 26.2231, "Ni61": 1.1399, "Ni62": 3.6345, "Ni64": 0.9256,
    "Cu63": 69.15, "Cu65": 30.85, "Nb93": 100.0,
    "Mo92": 14.53, "Mo94": 9.15, "Mo95": 15.84, "Mo96": 16.67, "Mo97": 9.60, "Mo98": 24.39, "Mo100": 9.82,
    "Ta180m1": 0.012, "Ta181": 99.988, "W180": 0.12, "W182": 26.50, "W183": 14.31, "W184": 30.64, "W186": 28.43}
STRUCTURAL_ELEMENTS = ("Fe", "Cr", "Ni", "W", "V", "Ti", "Ta", "Mo", "Nb", "Mn", "Co", "Cu", "Al", "Si", "Be", "Li")

SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr "
           "Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os "
           "Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg "
           "Cn Nh Fl Mc Lv Ts Og").split()
Z_OF = {sym: i + 1 for i, sym in enumerate(SYMBOLS)}
ISOMER_SUFFIX = {"": 0, "m": 1, "n": 2, "o": 3}
TENDL_NAME = re.compile(r"n-([A-Z][a-z]?)(\d{3})([mno]?)\.tendl")
GateError = p154.GateError


# ------------------------------------------------------------------------------ helpers


def sha256(path):
    return p154.sha256(path)


def canon(value):
    """Canonical JSON of a row (sorted keys, no spaces, no NaN)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def standard_ed(element):
    return STANDARD_ED.get(element, DEFAULT_ED)


def timed(command, timeout, env=None, cwd=ROOT):
    """One bounded subprocess (no shell). Returns (exit code, stdout, stderr, wall seconds, children's peak RSS kB).
    The peak is that of the largest child so far in this process (RUSAGE_CHILDREN is a running maximum)."""
    start = time.monotonic()
    try:
        proc = subprocess.run([str(c) for c in command], cwd=str(cwd), capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise GateError(f"timed out after {timeout} s: {' '.join(str(c) for c in command)[:200]}")
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start, peak


def sh(command, timeout, env=None):
    code, out, err, _t, _p = timed(command, timeout, env)
    return code, out, err


def copy_regular(src, dst):
    """A regular-file copy (the builder refuses symlinks)."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    shutil.copyfile(src, dst)
    if dst.is_symlink() or not dst.is_file():
        raise GateError(f"{dst} is not a regular file")
    return dst


def stage_corpus(directory, files, tendl_dir):
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for name in files:
        source = PB208_WORKING if name == PB208_FILE else Path(tendl_dir) / name
        copy_regular(source, directory / name)
        if name == PB208_FILE and sha256(directory / name) != PB208_WORKING_SHA256:
            raise GateError(f"{source} is not the Amendment C Pb-208 working file")
    return directory


def build_table(binary, directory, out, extra, timeout):
    out = Path(out)
    if out.exists():
        out.unlink()
    code, stdout, err, wall, peak = timed([binary, "build-damage", directory, out] + list(extra), timeout)
    if code != 0 or not out.exists():
        raise GateError(f"build-damage {directory} failed (exit {code}): {err.strip()[-400:]}")
    return json.loads(out.read_text()), {"wall_s": wall, "child_peak_rss_kB": peak, "stdout": stdout.replace(str(out), "<OUT>")}


def ed_args(energies):
    out = []
    for el, ed in energies.items():
        out += ["--displacement-energy", f"{el}={ed:g}"]
    return out


# ------------------------------------------------------------------------------ decay records and the selection


def endf_number(text):
    """An ENDF-6 field: blank is zero; `1.234567+3` is 1.234567e3."""
    t = text.strip()
    if not t:
        return 0.0
    try:
        return float(t)
    except ValueError:
        m = re.fullmatch(r"([+-]?[0-9]*\.?[0-9]*)([+-][0-9]+)", t)
        if not m:
            raise ValueError(f"unreadable ENDF number {text!r}")
        return float(f"{m.group(1)}e{m.group(2)}")


def parse_decay_lines(lines):
    """MF=8 MT=457 records of an ENDF-6 decay tape: {(Z, A, LISO): {"nst", "t_half_s", "mat", "za"}}. HEAD fields
    ZA, AWR, LIS, LISO, NST, NSP (LISO in columns 34-44, NST in 45-55); the next record's first field is T1/2 in s.
    A repeated nuclide keeps its first record and is counted in the `_duplicates` key of the returned dict's meta."""
    records, duplicates, pending = {}, 0, None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if len(line) < 75 or line[70:72] != " 8" or line[72:75] != "457":
            pending = None
            continue
        seq = line[75:80].strip()
        if seq == "1":
            za = int(round(endf_number(line[0:11])))
            pending = {"za": za, "liso": int(endf_number(line[33:44])), "nst": int(endf_number(line[44:55])),
                       "mat": line[66:70].strip()}
        elif seq == "2" and pending is not None:
            key = (pending["za"] // 1000, pending["za"] % 1000, pending["liso"])
            if key in records:
                duplicates += 1
            else:
                records[key] = {"nst": pending["nst"], "t_half_s": endf_number(line[0:11]), "mat": pending["mat"],
                                "za": pending["za"]}
            pending = None
    records.setdefault("_meta", {})["duplicates"] = duplicates
    return records


def read_decay(path):
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        return parse_decay_lines(handle)


def parse_tendl_name(name):
    """(symbol, A, isomer) of n-<El><AAA>[m|n|o].tendl, or None."""
    m = TENDL_NAME.fullmatch(name)
    if not m or m.group(1) not in Z_OF:
        return None
    return m.group(1), int(m.group(2)), ISOMER_SUFFIX[m.group(3)]


def nuclide_name(symbol, a, liso):
    return f"{symbol}{a}" + (f"m{liso}" if liso else "")


def select_targets(names, primary, fallback, year_s=YEAR_S):
    """The protocol's item-4 selection from TENDL file names and the two decay record sets. Returns
    {"selected": [rows], "excluded": [{"file", "reason"}]}; rows carry file, nuclide, source, stable, t_half_s."""
    selected, excluded = [], []
    for name in sorted(names):
        parsed = parse_tendl_name(name)
        if parsed is None:
            excluded.append({"file": name, "reason": "not a TENDL neutron file name"})
            continue
        symbol, a, liso = parsed
        key = (Z_OF[symbol], a, liso)
        for source, table in (("primary", primary), ("fallback", fallback)):
            rec = table.get(key)
            if rec is not None:
                break
        else:
            excluded.append({"file": name, "reason": "no MF=8 MT=457 record in either decay file"})
            continue
        stable = rec["nst"] == 1
        if stable or rec["t_half_s"] >= year_s:
            selected.append({"file": name, "nuclide": nuclide_name(symbol, a, liso), "source": source,
                             "stable": stable, "t_half_s": rec["t_half_s"]})
        else:
            excluded.append({"file": name, "reason": f"half-life {rec['t_half_s']:g} s below one year ({source})"})
    return {"selected": selected, "excluded": excluded}


def normalize_files(files):
    """`files` of a table as {basename: sha256 or None}; accepts a list of {path|name|file, sha256}, strings, or a map."""
    out = {}
    if isinstance(files, dict):
        for name, value in files.items():
            out[Path(name).name] = value.get("sha256") if isinstance(value, dict) else value
        return out
    for entry in files or []:
        if isinstance(entry, str):
            out[Path(entry).name] = None
        elif isinstance(entry, dict):
            name = entry.get("path") or entry.get("name") or entry.get("file")
            if name is None:
                raise ValueError(f"files entry without a name: {entry}")
            out[Path(name).name] = entry.get("sha256")
    return out


ROW_REQUIRED = ("damage_energy_barn_eV", "elastic", "inelastic", "disappearance", "uncovered_xs_barn",
                "uncovered_damage_upper_barn_eV", "displacement_nrt_barn")
ROW_OPTIONAL = ("displacement_arc_barn",)


def row_problems(row, n_groups=709, tol=TOL_SUM):
    """Finite, nonnegative, right length, and damage_energy_barn_eV == elastic + inelastic + disappearance per group
    within `tol` relative. Returns a list of problem strings (empty when the row is sound)."""
    problems = []
    for key in ROW_REQUIRED + tuple(k for k in ROW_OPTIONAL if k in row):
        values = row.get(key)
        if not isinstance(values, list) or len(values) != n_groups:
            problems.append(f"{key}: missing or not {n_groups} long")
        elif any((not isinstance(v, (int, float))) or not math.isfinite(v) or v < 0.0 for v in values):
            problems.append(f"{key}: negative or non-finite value")
    if problems:
        return problems
    worst, where = 0.0, None
    for g, (t, e, i, d) in enumerate(zip(row["damage_energy_barn_eV"], row["elastic"], row["inelastic"],
                                         row["disappearance"])):
        s = e + i + d
        scale = max(abs(t), abs(s))
        err = 0.0 if scale == 0.0 else abs(t - s) / scale
        if err > worst:
            worst, where = err, g
    if worst > tol:
        problems.append(f"damage_energy_barn_eV differs from the partial sum by {worst:.3g} (group {where})")
    return problems


def find_uncovered_entry(uncovered, nuclide, filename):
    """The `uncovered` entry naming the nuclide or file, with whether it carries a non-empty reason."""
    for entry in uncovered or []:
        text = json.dumps(entry) if not isinstance(entry, str) else entry
        if nuclide in re.findall(r"[A-Za-z]+[0-9]+(?:m[0-9]+)?", text) or filename in text:
            if isinstance(entry, dict):
                reason = next((str(v) for k, v in entry.items() if re.search(r"reason|why|note|error|detail", k, re.I)
                               and str(v).strip()), "")
            else:
                reason = entry if len(entry.strip()) > len(nuclide) + 2 else ""
            return {"entry": entry, "reason": reason, "has_reason": bool(reason.strip())}
    return None


def failed_map(table):
    """The table's top-level `failed` array as {file basename: entry} (Amendment B2)."""
    out = {}
    for entry in table.get("failed") or []:
        if isinstance(entry, dict) and (entry.get("file") or entry.get("path")):
            out[Path(entry.get("file") or entry.get("path")).name] = entry
    return out


def clipped_records(row):
    """The `channels.zero_k_clipped` array of a row (Amendment B1), [] when absent."""
    clipped = (row.get("channels") or {}).get("zero_k_clipped") or []
    return clipped if isinstance(clipped, list) else []


def element_of_nuclide(name):
    return re.match(r"[A-Z][a-z]?", name).group(0)


def failure_report(selected, rows, failed):
    """Amendment B2 accounting. `selected` rows carry file, nuclide, stable. Returns counts, per-target details with the
    natural abundance, the failed targets that are stable nuclides of the structural elements (these fail G5), failed
    entries naming a file outside the selection, and the selected targets satisfied by neither a row nor a failed entry."""
    failed_targets = [r for r in selected if r["file"] in failed and r["nuclide"] not in rows]
    clipped_targets = [(r, clipped_records(rows[r["nuclide"]])) for r in selected if r["nuclide"] in rows
                       and clipped_records(rows[r["nuclide"]])]
    names = {r["file"] for r in selected}

    def info(r, extra):
        return dict({"nuclide": r["nuclide"], "file": r["file"],
                     "natural_abundance_percent": NATURAL_ABUNDANCE_PERCENT.get(r["nuclide"])}, **extra)
    structural = [r["nuclide"] for r in failed_targets
                  if r["stable"] and element_of_nuclide(r["nuclide"]) in STRUCTURAL_ELEMENTS]
    return {"failed_count": len(failed_targets), "clipped_count": len(clipped_targets),
            "clipped_records": sum(len(c) for _r, c in clipped_targets),
            "failed": [info(r, {"error": str(failed[r["file"]].get("error", ""))[:300]}) for r in failed_targets],
            "clipped": [info(r, {"zero_k_clipped": c}) for r, c in clipped_targets],
            "failed_stable_structural": structural,
            "failed_entries_outside_selection": sorted(set(failed) - names),
            "failed_with_row": sorted(r["nuclide"] for r in selected if r["file"] in failed and r["nuclide"] in rows),
            "failed_without_error": sorted(f for f, e in failed.items() if not str(e.get("error", "")).strip())}


def compare_rows(shipped, rebuilt, keys):
    """Canonical-JSON comparison of rows keyed by nuclide; returns {"identical": [...], "different": [...], ...}."""
    identical, different, only_shipped, only_rebuilt = [], [], [], []
    for key in keys:
        a, b = shipped.get(key), rebuilt.get(key)
        if a is None and b is None:
            continue
        if a is None:
            only_rebuilt.append(key)
        elif b is None:
            only_shipped.append(key)
        elif canon(a) == canon(b):
            identical.append(key)
        else:
            different.append(key)
    return {"identical": identical, "different": different, "only_in_shipped": only_shipped,
            "only_in_rebuild": only_rebuilt,
            "pass": not different and not only_shipped and not only_rebuilt and bool(identical)}


def parse_time_log(text):
    """Wall time (s), peak RSS (kB) of GNU `time -v` output; absent values are None."""
    out = {"wall_s": None, "peak_rss_kB": None}
    m = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\):\s*([0-9:.]+)", text)
    if m:
        seconds = 0.0
        for part in m.group(1).split(":"):
            seconds = seconds * 60.0 + float(part)
        out["wall_s"] = seconds
    m = re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", text)
    if m:
        out["peak_rss_kB"] = int(m.group(1))
    return out


# ------------------------------------------------------------------------------ energy-source field


def strip_energy_source(node, pattern=None, path="", found=None):
    """Copy of `node` without the energy-source key (exact name); `found` collects (path, value) of the removed."""
    if not isinstance(pattern, re.Pattern):
        pattern = re.compile("^" + re.escape(pattern or ENERGY_SOURCE_KEY) + "$")
    found = [] if found is None else found
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if pattern.search(key):
                found.append((f"{path}/{key}", value))
                continue
            out[key] = strip_energy_source(value, pattern, f"{path}/{key}", found)[0]
        return out, found
    if isinstance(node, list):
        return [strip_energy_source(v, pattern, f"{path}[{i}]", found)[0] for i, v in enumerate(node)], found
    return node, found


def source_values(found):
    """Every leaf string among the removed energy-source values."""
    values = []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            values.append(v)
    for _path, value in found:
        walk(value)
    return values


def json_diff_paths(a, b, path="", out=None, limit=30):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}/{key}: present on one side only")
            else:
                json_diff_paths(a[key], b[key], f"{path}/{key}", out, limit)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff_paths(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {str(a)[:60]} != {str(b)[:60]}")
    return out


# ------------------------------------------------------------------------------ catalog


def catalog_checks(old, new, repo_notice_dir=CATALOG_DIR):
    """Structure of catalog 1.2.0 against catalog 1.1.0; returns {"problems": [...], ...}."""
    problems = []
    if new.get("catalog_version") != "1.2.0":
        problems.append(f"catalog_version {new.get('catalog_version')!r}")
    if not str(new.get("release_url", "")).endswith("data-v1.2.0"):
        problems.append(f"release_url {new.get('release_url')!r}")
    notice = new.get("notice")
    if notice != "ACTINV-DATA-NOTICE-v1.2.0.md":
        problems.append(f"notice {notice!r}")
    elif repo_notice_dir is not None and not (Path(repo_notice_dir) / notice).is_file():
        problems.append(f"notice file {notice} is not beside the catalog")
    new_by_id = {a["id"]: a for a in new.get("artifacts", [])}
    unchanged = 0
    for art in old.get("artifacts", []):
        other = new_by_id.get(art["id"])
        if other is None:
            problems.append(f"v1.1.0 artifact {art['id']} missing")
            continue
        diffs = [k for k in ("id", "role", "path", "bytes", "sha256", "source") if art.get(k) != other.get(k)]
        if diffs:
            problems.append(f"v1.1.0 artifact {art['id']} changed: {diffs}")
        else:
            unchanged += 1
    added = sorted(set(new_by_id) - {a["id"] for a in old.get("artifacts", [])})
    tables = [a for a in new.get("artifacts", []) if a.get("role") == CATALOG_ROLE]
    if [t["id"] for t in tables] != [CATALOG_ID]:
        problems.append(f"damage-table artifacts {[t['id'] for t in tables]}")
    for t in tables:
        if t.get("licence") != "CC-BY-4.0":
            problems.append(f"{t['id']} licence {t.get('licence')!r}")
        if not isinstance(t.get("bytes"), int) or t["bytes"] <= 0 or not re.fullmatch(r"[0-9a-f]{64}", str(t.get("sha256"))):
            problems.append(f"{t['id']} bytes / sha256 malformed")
        src = t.get("source", {})
        if "/data-v1.2.0/" not in str(src.get("url")) or src.get("bytes") != t.get("bytes") \
                or src.get("sha256") != t.get("sha256"):
            problems.append(f"{t['id']} source {src}")
    bundles = {b["id"]: b for b in new.get("bundles", [])}
    for b in new.get("bundles", []):
        for aid in b.get("artifacts", []):
            if aid not in new_by_id:
                problems.append(f"bundle {b['id']} lists unknown artifact {aid}")
        has = CATALOG_ID in b.get("artifacts", [])
        want = b["id"] in NEUTRON_BUNDLES
        if has != want:
            problems.append(f"bundle {b['id']} {'lacks' if want else 'lists'} {CATALOG_ID}")
    for bid in NEUTRON_BUNDLES:
        if bid not in bundles:
            problems.append(f"neutron bundle {bid} missing")
    old_bundles = {b["id"]: b for b in old.get("bundles", [])}
    old_roles = {a["id"]: a.get("role") for a in old.get("artifacts", [])}
    notices_superseded = {}
    for bid, b in old_bundles.items():
        nb = bundles.get(bid)
        if nb is None:
            problems.append(f"v1.1.0 bundle {bid} missing")
        else:
            lost = [a for a in b["artifacts"] if a not in nb["artifacts"]]
            # a notice may be superseded by a new notice in the same bundle (the v1.1.0 notice artifacts stay in the
            # catalog, byte-identical; protocol G7 requires the artifacts, not their bundle membership, unchanged)
            gained_notice = [a for a in nb["artifacts"] if a in added and new_by_id[a].get("role") == "notice"]
            superseded = [a for a in lost if old_roles.get(a) == "notice" and gained_notice]
            if superseded:
                notices_superseded[bid] = {"dropped": superseded, "by": gained_notice}
            lost = [a for a in lost if a not in superseded]
            if lost:
                problems.append(f"bundle {bid} lost artifacts {lost}")
    return {"problems": problems, "v110_artifacts_unchanged": unchanged, "added_artifacts": added,
            "notices_superseded": notices_superseded, "pass": not problems}


def parse_sums(text):
    """`SHA256SUMS` lines `<hex>  <name>` -> {name: hex}."""
    out = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[parts[1].strip().lstrip("*")] = parts[0].lower()
    return out


def parse_sizes(text):
    """`SIZES` lines, `<name> <bytes>` or `<bytes> <name>` -> {name: bytes}."""
    out = {}
    for line in text.splitlines():
        parts = line.replace("=", " ").replace(":", " ").split()
        if len(parts) != 2:
            continue
        a, b = parts
        if a.isdigit() and not b.isdigit():
            out[b.lstrip("*")] = int(a)
        elif b.isdigit() and not a.isdigit():
            out[a.lstrip("*")] = int(b)
    return out


def downloaded_in_output(text, artifacts):
    """Artifacts whose id or file name appears on a line that reports a download (not a reuse / copy / skip)."""
    hit = []
    for line in text.splitlines():
        if not re.search(r"download|fetch(ed|ing)\b", line, re.I):
            continue
        if re.search(r"reus|copi|skip|already|exist|present|verif", line, re.I):
            continue
        for art in artifacts:
            if art["id"] in line or Path(art["path"]).name in line:
                hit.append(art["id"])
    return sorted(set(hit))


# ------------------------------------------------------------------------------ P154 parameterization (wrapping)


class p154_targets:
    """Context manager: check_p154's TARGETS / BUILD_ED rebound to this phase's targets and restored on exit."""

    def __init__(self, targets, ed_map):
        self.targets, self.ed_map = targets, ed_map

    def __enter__(self):
        self.saved = (dict(p154.TARGETS), dict(p154.BUILD_ED), p154.build_table)
        p154.TARGETS.clear()
        p154.TARGETS.update(self.targets)
        p154.BUILD_ED.clear()
        p154.BUILD_ED.update(self.ed_map)
        return self

    def __exit__(self, *exc):
        p154.TARGETS.clear()
        p154.TARGETS.update(self.saved[0])
        p154.BUILD_ED.clear()
        p154.BUILD_ED.update(self.saved[1])
        p154.build_table = self.saved[2]
        return False


def g4_target_table():
    """check_p154.TARGETS-shaped entries for the twelve G4 targets and their standard energies."""
    targets = {t: {"file": f, "element": el, "sha256": sha} for t, (f, el, sha) in G4_TARGETS.items()}
    return targets, {el: standard_ed(el) for (_f, el, _s) in G4_TARGETS.values()}


def limit_modes(mode_information):
    """Wrap p154.build_table so only each target's first (NJOY-matching) build runs unless mode information is wanted;
    the other capture / continuum combinations of P154 are information and raise GateError (recorded as skipped)."""
    original = p154.build_table
    seen = set()

    def wrapped(binary, target, work, ed_map, capture, grid_density, timeout, reuse, continuum="kinematic"):
        if not mode_information and target in seen:
            raise GateError("information-only capture / continuum combination skipped (--mode-information)")
        seen.add(target)
        return original(binary, target, work, ed_map, capture, grid_density, timeout, reuse, continuum)
    p154.build_table = wrapped


# ------------------------------------------------------------------------------ Amendment D (G4 references)

D1_SIDE = 1e-9                  # relative offset of the left-hand W point at an LTT=3 switch energy
D3_MIN_UNCOVERED = 10           # groups excluded as uncovered that make a vacuous comparison "reported"
D_TAG = "P156-D"                # cache-key tag of the Amendment D references
_P154_W_TABLE = p154.w_table
_P154_LEVEL_W_TABLES = p154.level_w_tables
_P154_B_VERSION = p154.B_VERSION
_HEATR_GL = []                  # 64-point Gauss-Legendre rule (disbar's nq = 64), computed once


def w_table_d1(mf4, ed, refine=p154.EL_REFINE):
    """Amendment D1: p154.w_table, plus the Legendre (left-hand) W just below the switch energy E_t of an LTT=3
    section. ENDF uses the Legendre representation up to E_t and the tabulated one above, so W steps at E_t; P154's
    table keeps only the tabulated value at E_t and the linear W then spreads the step over the last refined interval
    below it."""
    out = _P154_W_TABLE(mf4, ed, refine)
    if mf4["ltt"] != 3 or len(mf4["parts"]) != 2:
        return out
    legendre = mf4["parts"][0]
    switch = legendre["energies"][-1]
    awr, z = mf4["awr"], mf4["za"] // 1000
    left = switch * (1.0 - D1_SIDE)
    if left <= ed * (awr + 1.0) ** 2 / (4.0 * awr) or not out["x"] or out["x"][-1] < switch:
        return out
    value = p154.elastic_w(p154.legendre_pdf(legendre["data"][-1]), switch, awr, z, ed, lab=mf4["lct"] == 1)
    points = dict(zip(out["x"], out["y"]))
    points[left] = value
    xs = sorted(points)
    info = dict(out["info"], n_points=len(xs), ltt3_switch_eV=switch, ltt3_left_W=value,
                ltt3_right_W=points.get(switch), amendment="P156 D1: W steps at the LTT=3 switch energy")
    return {"x": xs, "y": [points[x] for x in xs], "ranges": [(len(xs), 2)], "info": info}


def heatr_level_w(pdf, e, awr, z, ed, thresh):
    """Amendment D2: disbar's damage per event of a discrete level at `e` -- the 64-point Gauss-Legendre rule over the
    centre-of-mass cosine of pdf(mu) R(T(mu)), T = A E (1 + r^2 - 2 r mu) / (A + 1)^2, negative sums set to zero
    (heatr.f90 disbar label 120). The pdf is the one p154.inelastic_w integrates exactly."""
    if not _HEATR_GL:
        _HEATR_GL.extend(zip(*p154.gauss_legendre(64)))
    _breaks, fn = pdf
    r = 0.0 if thresh >= e * (1.0 - p154.LADDER_SMALL) else math.sqrt(1.0 - thresh / e)
    c = awr * e / (awr + 1.0) ** 2
    total = sum(w * fn(u) * p154.df(c * (1.0 + r * r - 2.0 * r * u), z, awr, z, awr, ed) for u, w in _HEATR_GL)
    return max(total, 0.0)


def level_w_tables_d2(mf4, awr, z, ed, thresh, grid):
    """Amendment D2: p154.level_w_tables with W_ladder from HEATR's 64-point rule at the ladder points (W_exact
    unchanged)."""
    exact, lad, ladder = _P154_LEVEL_W_TABLES(mf4, awr, z, ed, thresh, grid)
    lad = dict(lad, y=[heatr_level_w(p154.mf4_pdf_at(mf4, e), e, awr, z, ed, thresh) for e in ladder])
    return exact, lad, ladder


def exact_elastic_cached_d1(target, work, record, ed, boundaries, reuse):
    """p154.exact_elastic_cached with the D1 reference and a cache key that names it."""
    cache = work / f"exact_elastic_{target}.json"
    key = {"pendf_sha256": record.get("pendf_sha256"), "endf_sha256": record["endf"]["sha256"], "ed_eV": ed,
           "amendment": D_TAG + "1"}
    if reuse and cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get("key") == key:
            return saved["result"]
    result = p154.exact_elastic_reference(record["endf"]["path"], record["pendf"], boundaries, ed)
    cache.write_text(json.dumps({"key": key, "result": result}))
    return result


@contextlib.contextmanager
def amendment_d_references():
    """Rebind the P154 reference functions to their Amendment D1/D2 forms for the duration (check_p154.py itself is
    sealed by the P154 verdict and is not edited)."""
    saved = (p154.w_table, p154.level_w_tables, p154.exact_elastic_cached, p154.B_VERSION)
    p154.w_table, p154.level_w_tables, p154.exact_elastic_cached = w_table_d1, level_w_tables_d2, exact_elastic_cached_d1
    p154.B_VERSION = f"{_P154_B_VERSION}+{D_TAG}2"
    try:
        yield
    finally:
        p154.w_table, p154.level_w_tables, p154.exact_elastic_cached, p154.B_VERSION = saved


def apply_d3(case):
    """Amendment D3 on one target's G4 case: a channel comparison that is vacuous only because its groups are
    uncovered (no group outside tolerance, at least D3_MIN_UNCOVERED groups excluded as uncovered) is reported, not
    gating, and so is its precondition set when that has no group outside its bounds. Returns the case with
    `amendment_d3` recorded and `pass` recomputed."""
    if "inelastic_vs_446" not in case:
        return case
    reported = {}
    for channel, pre in (("inelastic_vs_446", "b2_precondition"), ("disappearance_vs_447", "b1_precondition")):
        ch, pc = case[channel], case[pre]
        uncovered_vacuous = (ch.get("vacuous") is True and not ch.get("bad")
                             and ch.get("with_uncovered_reported", {}).get("n", 0) >= D3_MIN_UNCOVERED)
        if uncovered_vacuous:
            reported[channel] = {"evaluated_groups": ch.get("evaluated_groups"),
                                 "uncovered_groups": ch["with_uncovered_reported"]["n"]}
            if not pc.get("pass") and not pc.get("bad"):
                reported[pre] = {"set_size": pc.get("set_size"), "vacuous": pc.get("vacuous")}
    gating = [k for k in ("inelastic_vs_446", "disappearance_vs_447", "b2_precondition", "b1_precondition")
              if k not in reported]
    case["amendment_d3"] = {"reported_not_gating": reported, "gating": gating}
    case["pass"] = all(case[k].get("pass") is True for k in gating)
    return case


def p154_recorded_modes():
    """Capture / continuum mode per P154 target as recorded in results/p154_damage.json, else from the files."""
    modes = {}
    recorded = {}
    if P154_RESULT.is_file():
        recorded = json.loads(P154_RESULT.read_text()).get("evidence", {}).get("targets", {})
    for target, info in p154.TARGETS.items():
        rec = recorded.get(target, {})
        if "capture_determination" in rec and "continuum_determination" in rec:
            modes[target] = (rec["capture_determination"]["mode"], rec["continuum_determination"]["mode"],
                             "results/p154_damage.json")
        else:
            path = p154.TENDL_DIR / info["file"]
            modes[target] = (p154.njoy_capture_treatment(path)["mode"], p154.njoy_continuum_recoil(path)["mode"],
                             "scanned from the evaluation")
    return modes


# ------------------------------------------------------------------------------ G0


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    out, ok = {}, True
    for label, path in (("protocol", PROTOCOL), ("amendment_a", AMENDMENT_A), ("amendment_b", AMENDMENT_B),
                        ("amendment_c", AMENDMENT_C), ("amendment_d", AMENDMENT_D)):
        if not path.is_file():
            out[label] = {"path": str(path), "present": False, "in_protocol_hash_txt": False}
            ok = False
            continue
        digest = sha256(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[label] = {"path": str(path), "sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    out["protocol_sha256"] = out["protocol"].get("sha256")
    out["pass"] = ok
    return out


# ------------------------------------------------------------------------------ G1 / G8


def gate_g1(log):
    out = {"pass": None, "external": True, "note": "fmt, clippy -D warnings, workspace tests (run by the coordinator)"}
    if log:
        path = Path(log).expanduser()
        if path.is_file():
            out["log"] = {"path": str(path), "sha256": sha256(path), "tail": path.read_text(errors="replace")[-600:]}
        else:
            out["log"] = {"path": str(path), "error": "absent"}
    return out


# ------------------------------------------------------------------------------ G2


def gate_g2(ref, cand, work, timeout, reuse):
    work = Path(work) / "g2"
    work.mkdir(parents=True, exist_ok=True)
    base = p154.gate_g2(ref, cand, work, timeout)
    cases = dict(base["cases"])
    modes = p154_recorded_modes()
    four = {}
    for target, info in p154.TARGETS.items():
        path = p154.TENDL_DIR / info["file"]
        found = sha256(path) if path.is_file() else None
        capture, continuum, how = modes[target]
        args = p154.build_args(target, p154.BUILD_ED, capture, None, continuum)
        outs = {}
        for label, binary in (("ref", ref), ("cand", cand)):
            out = work / f"{target}.{label}.json"
            if not (reuse and out.exists()):
                if out.exists():
                    out.unlink()
                code, stdout, err = sh([binary, "build-damage", path, out] + args, timeout)
                if code != 0 or not out.exists():
                    raise GateError(f"{label} build-damage {target} failed: {err.strip()[-300:]}")
                (work / f"{target}.{label}.stdout").write_text(stdout.replace(str(out), "<OUT>"))
            outs[label] = (out.read_bytes(), (work / f"{target}.{label}.stdout").read_text())
        four[target] = {"input_sha256": found, "pinned_sha256": info["sha256"], "input_matches_pin": found == info["sha256"],
                        "args": [str(a) for a in args], "modes_from": how,
                        "output_byte_identical": outs["ref"][0] == outs["cand"][0],
                        "stdout_identical": outs["ref"][1] == outs["cand"][1],
                        "output_sha256": hashlib.sha256(outs["cand"][0]).hexdigest()}
        four[target]["pass"] = (four[target]["input_matches_pin"] and four[target]["output_byte_identical"]
                                and four[target]["stdout_identical"])
        cases[f"p154_target_{target}"] = four[target]
    ok = all(c.get("pass") is True for c in cases.values())
    return {"pass": ok, "cases": cases}


# ------------------------------------------------------------------------------ G3


def arc_nrt_comparison(arc_a, arc_b, nrt_a, nrt_b, tol_arc=TOL_ARC, tol_nrt=TOL_NRT, floor=A1_FLOOR):
    """Amendment A1 for one element built at two NRT energies (a = default, b = the comparison energy). arc: relative
    difference of a from b at most tol_arc in every group where arc_b exceeds floor x its maximum. NRT: relative
    difference from nrt_b more than tol_nrt in every group where nrt_b exceeds floor x its maximum. Reports the largest
    arc and the smallest NRT difference."""
    if not (len(arc_a) == len(arc_b) == len(nrt_a) == len(nrt_b)) or not arc_b:
        return {"pass": False, "error": "columns missing or of different length"}
    arc_top, nrt_top = max(arc_b), max(nrt_b)
    arc_d = [(p154.rel(a, b), g) for g, (a, b) in enumerate(zip(arc_a, arc_b)) if b > floor * arc_top]
    nrt_d = [(p154.rel(a, b), g) for g, (a, b) in enumerate(zip(nrt_a, nrt_b)) if b > floor * nrt_top]
    arc_max = max(arc_d, default=(math.inf, None))
    nrt_min = min(nrt_d, default=(0.0, None))
    return {"arc_groups": len(arc_d), "nrt_groups": len(nrt_d), "largest_arc_rel_diff": arc_max[0],
            "largest_arc_group": arc_max[1], "smallest_nrt_rel_diff": nrt_min[0], "smallest_nrt_group": nrt_min[1],
            "arc_bitwise_identical": arc_a == arc_b,
            "pass": bool(arc_d and nrt_d and arc_max[0] <= tol_arc and nrt_min[0] > tol_nrt)}


def smallest_per_element(tendl_dir, skip_elements):
    """{element: file name} of the smallest ground-state TENDL file of each standard-table element not in skip."""
    best = {}
    for p in Path(tendl_dir).iterdir():
        parsed = parse_tendl_name(p.name)
        if parsed is None or parsed[2] != 0 or parsed[0] not in STANDARD_ED or parsed[0] in skip_elements:
            continue
        size = p.stat().st_size
        if parsed[0] not in best or size < best[parsed[0]][0]:
            best[parsed[0]] = (size, p.name)
    return {el: name for el, (_s, name) in best.items()}


def gate_g3(ref, cand, work, tendl_dir, timeout):
    work = Path(work) / "g3"
    work.mkdir(parents=True, exist_ok=True)
    named = ["n-Fe056.tendl", "n-W184.tendl", "n-Be009.tendl", "n-Li007.tendl", "n-Cu063.tendl"]
    elements = {parse_tendl_name(n)[0] for n in named}
    extra = smallest_per_element(tendl_dir, elements)
    files = named + sorted(extra.values())
    present = {parse_tendl_name(n)[0] for n in files}
    cases = {}
    corpus = stage_corpus(work / "corpus", files, tendl_dir)
    table, meta = build_table(cand, corpus, work / "corpus.json", G3_ARGS, timeout)
    got = table.get("displacement", {}).get("displacement_energy_eV", {})
    want = {el: standard_ed(el) for el in present}
    cases["standard_table"] = {"files": files, "expected": want, "recorded": got, "pass": got == want,
                               "elements_not_in_standard_table_get_25": {el: got.get(el) for el in present
                                                                         if el not in STANDARD_ED},
                               "build": {k: meta[k] for k in ("wall_s", "child_peak_rss_kB")}}
    rows = table.get("targets", {})

    # W-184: 90 (default) against 70
    w_dir = stage_corpus(work / "w184", ["n-W184.tendl"], tendl_dir)
    w70, _ = build_table(cand, w_dir, work / "w184_70.json", G3_ARGS + ed_args({"W": 70.0}), timeout)
    a, b = rows.get("W184", {}), w70["targets"]["W184"]
    cmp = arc_nrt_comparison(a.get("displacement_arc_barn", []), b.get("displacement_arc_barn", []),
                             a.get("displacement_nrt_barn", []), b.get("displacement_nrt_barn", []))
    cmp.update(default_W=got.get("W"), w70_recorded=w70["displacement"]["displacement_energy_eV"],
               amendment="Amendment A1: arc within 1e-3, NRT differs by more than 1e-2, groups above 1e-6 of the maximum")
    cmp["pass"] = bool(cmp["pass"] and got.get("W") == 90.0)
    cases["w184_arc_independent_of_ed"] = cmp

    # Fe-56 at Fe=40: arc column equals the reference binary's; the no-option candidate build is the same row
    fe_dir = stage_corpus(work / "fe56", ["n-Fe056.tendl"], tendl_dir)
    fe_args = G3_ARGS + ed_args({"Fe": 40.0})
    fe_cand, _ = build_table(cand, fe_dir, work / "fe56_cand.json", fe_args, timeout)
    fe_ref, _ = build_table(ref, fe_dir, work / "fe56_ref.json", fe_args, timeout)
    rc, rr, rd = fe_cand["targets"]["Fe56"], fe_ref["targets"]["Fe56"], rows.get("Fe56", {})
    cases["fe56_arc_equals_reference"] = {
        "arc_identical": rc.get("displacement_arc_barn") == rr.get("displacement_arc_barn")
        and "displacement_arc_barn" in rc,
        "whole_row_identical": canon(rc) == canon(rr),
        "no_option_build_equals_explicit_40": canon(rd) == canon(rc)}
    cases["fe56_arc_equals_reference"]["pass"] = (cases["fe56_arc_equals_reference"]["arc_identical"]
                                                  and cases["fe56_arc_equals_reference"]["no_option_build_equals_explicit_40"])

    # Cu-63: arc at the fit's own energy (33 eV) whatever the NRT energy
    cu_dir = stage_corpus(work / "cu63", ["n-Cu063.tendl"], tendl_dir)
    cu33, _ = build_table(cand, cu_dir, work / "cu63_33.json", G3_ARGS + ed_args({"Cu": 33.0}), timeout)
    c33, c40 = cu33["targets"]["Cu63"], rows.get("Cu63", {})
    ref_ed = cu33["displacement"]["arc_dpa"]["parameters"]["Cu"]["reference_displacement_energy_eV"]
    cmp = arc_nrt_comparison(c40.get("displacement_arc_barn", []), c33.get("displacement_arc_barn", []),
                             c40.get("displacement_nrt_barn", []), c33.get("displacement_nrt_barn", []))
    cmp.update(default_Cu=got.get("Cu"), reference_energy_recorded=ref_ed,
               note="same numerical form as Amendment A1 (the amendment names W-184; Cu-63 is this checker's addition)")
    cmp["pass"] = bool(cmp["pass"] and got.get("Cu") == 40.0 and ref_ed == 33.0)
    cmp["information"] = True       # reported, not gating: Amendment A1 names W-184 only
    cases["cu63_arc_at_reference_energy"] = cmp
    return {"pass": all(c["pass"] for c in cases.values() if not c.get("information")), "cases": cases}


# ------------------------------------------------------------------------------ G4


def gate_g4(cand, work, args, bounds):
    work = Path(work) / "g4"
    work.mkdir(parents=True, exist_ok=True)
    targets, ed_map = g4_target_table()
    inputs = {}
    for t, info in targets.items():
        path = Path(args.tendl_dir) / info["file"]
        found = sha256(path) if path.is_file() else None
        inputs[t] = {"path": str(path), "sha256": found, "pinned": info["sha256"], "match": found == info["sha256"]}
    if not all(v["match"] for v in inputs.values()):
        return {"pass": False, "error": "a G4 evaluation is absent or its SHA-256 differs from the pinned value",
                "inputs": inputs}
    spec = json.loads(p154.GATE_SPEC.read_text())
    ns = argparse.Namespace(ed_uniform=0.0, njoy=args.njoy, njoy_err=args.njoy_err, timeout=args.timeout,
                            reuse=args.reuse, grid_density=None)
    saved_dir = p154.TENDL_DIR
    p154.TENDL_DIR = Path(args.tendl_dir)
    try:
        with p154_targets(targets, ed_map), amendment_d_references():
            limit_modes(args.mode_information)
            g3, g4, g5, tables, evidence, used = p154.gates_g3_g4_g5(cand, work, ns, bounds, spec)
    finally:
        p154.TENDL_DIR = saved_dir
    for t in list(g4["cases"]):
        g4["cases"][t] = apply_d3(g4["cases"][t])
    g4["pass"] = len(g4["cases"]) == len(targets) and all(c.get("pass") is True for c in g4["cases"].values())
    g4["amendment_d"] = {"D1": "elastic W steps at an LTT=3 switch energy", "D2": "B2 W_ladder by HEATR's 64-point rule",
                         "D3": f"uncovered-vacuous comparisons reported (>= {D3_MIN_UNCOVERED} uncovered groups)"}
    per_target = {}
    for t in targets:
        entry = {"element": targets[t]["element"], "displacement_energy_eV": ed_map[targets[t]["element"]],
                 "elastic_pass": g3["cases"].get(t, {}).get("pass") is True,
                 "inelastic_disappearance_pass": g4["cases"].get(t, {}).get("pass") is True,
                 "total_bound_pass": g5["cases"].get(t, {}).get("pass") is True}
        ev = evidence["targets"].get(t, {})
        entry["capture_mode"] = ev.get("capture_determination", {}).get("mode")
        entry["continuum_mode"] = ev.get("continuum_determination", {}).get("mode")
        if t in tables:
            table, _path = tables[t]
            row = table["targets"][t]
            tb = [float(b) for b in table["boundaries_eV"]]
            flux = p154.spectrum_ascending(spec, tb)
            computed = p154.fold(row["damage_energy_barn_eV"], flux)
            upper = p154.fold(row["uncovered_damage_upper_barn_eV"], flux)
            entry["uncovered"] = {
                "fns_uncovered_share": upper / (computed + upper) if computed + upper > 0.0 else None,
                "fns_computed": computed, "fns_uncovered_upper": upper,
                "groups_with_uncovered_xs": sum(1 for v in row["uncovered_xs_barn"] if v > 0.0),
                "channels": {k: v for k, v in row.get("channels", {}).items() if "uncovered" in k.lower()}}
        if t in tables:
            entry["zero_k_clipped"] = clipped_records(tables[t][0]["targets"][t])    # information only (Amendment B3)
        entry["pass"] = entry["elastic_pass"] and entry["inelastic_disappearance_pass"] and entry["total_bound_pass"]
        per_target[t] = entry
    ok = bool(per_target) and all(e["pass"] for e in per_target.values()) and g3["pass"] and g4["pass"] and g5["pass"]
    return {"pass": ok, "per_target": per_target, "elastic": g3, "inelastic_disappearance": g4, "total_bound": g5,
            "inputs": inputs, "displacement_energy_eV": ed_map, "evidence": evidence}


# ------------------------------------------------------------------------------ G5


def locate_decay(args):
    """(primary path, fallback path, how) from --decay-primary / --decay-fallback or the catalog under --data-dir."""
    if args.decay_primary and args.decay_fallback:
        return Path(args.decay_primary).expanduser(), Path(args.decay_fallback).expanduser(), "command line"
    cat = json.loads(Path(args.catalog).read_text())
    roles = {a["role"]: a for a in cat["artifacts"] if a["role"] in ("decay-primary", "decay-fallback")}
    root = Path(args.data_dir).expanduser()
    out = []
    for role in ("decay-primary", "decay-fallback"):
        art = roles[role]
        rel_path = Path(art["path"])
        found = next((c for c in (root / f"v{cat['catalog_version']}" / rel_path, root / rel_path,
                                  root / "v1.1.0" / rel_path, LIBRARY_DIR / rel_path) if c.is_file()), None)
        if found is None:
            raise GateError(f"{role} file {rel_path} not found under {root}")
        if sha256(found) != art["sha256"]:
            raise GateError(f"{found} differs from the catalog SHA-256 of {art['id']}")
        out.append(found)
    return out[0], out[1], f"catalog {cat['catalog_version']} under {root}"


def gate_g5(cand, work, args):
    work = Path(work) / "g5"
    work.mkdir(parents=True, exist_ok=True)
    tendl = Path(args.tendl_dir)
    primary_path, fallback_path, how = locate_decay(args)
    primary, fallback = read_decay(primary_path), read_decay(fallback_path)
    names = sorted(p.name for p in tendl.iterdir() if p.name.startswith("n-") and p.name.endswith(".tendl"))
    selection = select_targets(names, primary, fallback)
    sel = selection["selected"]
    sizes = {r["file"]: (tendl / r["file"]).stat().st_size for r in sel}
    hashes = {r["file"]: (PB208_WORKING_SHA256 if r["file"] == PB208_FILE else sha256(tendl / r["file"]))
              for r in sel}
    out = {"decay": {"primary": str(primary_path), "primary_sha256": sha256(primary_path),
                     "fallback": str(fallback_path), "fallback_sha256": sha256(fallback_path), "located_by": how,
                     "primary_records": len(primary) - 1, "fallback_records": len(fallback) - 1},
           "tendl_files": len(names), "selected": len(sel), "selected_bytes": sum(sizes.values()),
           "selection_sha256": hashlib.sha256("".join(f"{r['file']} {hashes[r['file']]}\n" for r in sel).encode()).hexdigest(),
           "from_fallback": [r["file"] for r in sel if r["source"] == "fallback"],
           "excluded_no_record": [e["file"] for e in selection["excluded"] if e["reason"].startswith("no MF=8")],
           "excluded_short_lived": len([e for e in selection["excluded"] if e["reason"].startswith("half-life")]),
           "selected_files": [r["file"] for r in sel]}
    if not args.table:
        out.update({"pass": False, "error": "--table not given (the shipped table to check)"})
        return out
    table_path = Path(args.table).expanduser()
    table = json.loads(table_path.read_text())
    checks = {}
    checks["format"] = {"format": table.get("format"), "pass": table.get("format") == "actinv-damage-table-2"}
    tfiles = normalize_files(table.get("files"))
    want = {r["file"]: hashes[r["file"]] for r in sel}
    checks["files_equal_selection"] = {
        "missing_from_table": sorted(set(want) - set(tfiles))[:20], "extra_in_table": sorted(set(tfiles) - set(want))[:20],
        "sha256_mismatch": sorted(n for n in set(want) & set(tfiles) if tfiles[n] != want[n])[:20],
        "table_files": len(tfiles), "pass": tfiles == want}
    rows = table.get("targets", {})
    uncovered = table.get("uncovered", [])
    failed = failed_map(table)
    no_row, no_reason, bad_rows = [], [], {}
    for r in sel:
        if r["nuclide"] in rows:
            problems = row_problems(rows[r["nuclide"]])
            if problems:
                bad_rows[r["nuclide"]] = problems
        elif r["file"] in failed:
            pass
        else:
            entry = find_uncovered_entry(uncovered, r["nuclide"], r["file"])
            if entry is None:
                no_row.append(r["nuclide"])
            elif not entry["has_reason"]:
                no_reason.append(r["nuclide"])
    checks["every_target_row_or_listed"] = {"neither_row_nor_listed": no_row[:30], "listed_without_reason": no_reason[:30],
                                            "rows": len(rows), "listed": len(uncovered), "failed_entries": len(failed),
                                            "pass": not no_row and not no_reason}
    report = failure_report(sel, rows, failed)
    checks["failed_and_clipped_targets"] = dict(report, **{"pass": not report["failed_stable_structural"]
                                                           and not report["failed_without_error"]
                                                           and not report["failed_entries_outside_selection"]
                                                           and not report["failed_with_row"]})
    extra_rows = sorted(set(rows) - {r["nuclide"] for r in sel})
    checks["rows_sound"] = {"rows_checked": len(rows), "problems": dict(list(bad_rows.items())[:15]),
                            "extra_rows_not_in_selection": extra_rows[:20],
                            "pass": not bad_rows and not extra_rows}
    for nuc, row in rows.items():          # rows outside the selection are reported above; still check their soundness
        if nuc not in bad_rows and nuc in extra_rows:
            problems = row_problems(row)
            if problems:
                bad_rows[nuc] = problems
                checks["rows_sound"]["pass"] = False
    elems = {parse_tendl_name(r["file"])[0] for r in sel}
    rec = table.get("displacement", {}).get("displacement_energy_eV", {})
    checks["energies_standard"] = {"expected": {el: standard_ed(el) for el in sorted(elems)}, "recorded": rec,
                                   "pass": rec == {el: standard_ed(el) for el in elems}}
    size = table_path.stat().st_size
    log = {}
    if args.build_log and Path(args.build_log).expanduser().is_file():
        log = parse_time_log(Path(args.build_log).expanduser().read_text(errors="replace"))
    out["build_report"] = {"table_bytes": size, "table_sha256": sha256(table_path), "build_log": log,
                           "build_command": "actinv build-damage DIR OUT " + " ".join(ITEM6_ARGS)}

    # seeded subset rebuild with the frozen command
    subset = sorted(random.Random(SEED).sample([r["file"] for r in sel], min(SUBSET, len(sel))))
    directory = stage_corpus(work / "subset", subset, tendl)
    rebuilt, meta = build_table(cand, directory, work / "subset.json", ITEM6_ARGS, args.timeout)
    keys = [nuclide_name(*parse_tendl_name(n)) for n in subset]
    cmp_rows = compare_rows(rows, rebuilt.get("targets", {}), keys)
    only_listed = [k for k in keys if k not in rows and k not in rebuilt.get("targets", {})]
    cmp_rows["not_rows_in_either"] = only_listed
    cmp_rows["pass"] = cmp_rows["pass"] and not cmp_rows["different"]
    cmp_rows.update(subset_files=subset, seed=SEED, rebuild_wall_s=meta["wall_s"],
                    rebuild_child_peak_rss_kB=meta["child_peak_rss_kB"],
                    rebuild_energies_standard=all(
                        v == standard_ed(el) for el, v in rebuilt["displacement"]["displacement_energy_eV"].items()))
    cmp_rows["pass"] = cmp_rows["pass"] and cmp_rows["rebuild_energies_standard"]
    checks["subset_rebuild"] = cmp_rows
    out["checks"] = checks
    out["pass"] = all(c["pass"] for c in checks.values())
    return out


# ------------------------------------------------------------------------------ G6


def absolute_spec(spec, library_dir):
    """The relative `actinv-data/v1.1.0/...` library and decay paths of an example spec made absolute under library_dir."""
    spec = copy.deepcopy(spec)

    def fix(p):
        prefix = "actinv-data/v1.1.0/"
        return str(Path(library_dir) / p[len(prefix):]) if p.startswith(prefix) else str(ROOT / p)
    spec["library"]["path"] = fix(spec["library"]["path"])
    for key in ("primary", "fallback"):
        if spec.get("decay", {}).get(key):
            spec["decay"][key] = fix(spec["decay"][key])
    return spec


def effective_row(rows, weights):
    """Atom-weighted mean row over the covered isotopes (every observable of a run is linear in the row)."""
    keys = [k for k in ROW_REQUIRED + ROW_OPTIONAL if all(k in r for r in rows)]
    total = sum(weights)
    return {k: [sum(w * r[k][g] for w, r in zip(weights, rows)) / total for g in range(len(rows[0][k]))] for k in keys}


def fe_covered(table_rows):
    """Fe isotopes of the abundance copy that have a table row: (mass numbers, atom fractions, expected covered fraction,
    atoms per gram of the covered isotopes in a 1 g natural Fe material)."""
    covered = [a for a in sorted(p154.FE_ABUNDANCE) if f"Fe{a}" in table_rows]
    fracs = [p154.FE_ABUNDANCE[a][0] for a in covered]
    molar = sum(f * m for f, m in p154.FE_ABUNDANCE.values())
    total = sum(f for f, _m in p154.FE_ABUNDANCE.values())
    return covered, fracs, sum(fracs) / total, sum(fracs) / molar * p154.NA


def fns_run(cand, work, args, table_path, label="fns"):
    """The FNS Fe run on a table, reproduced from the rows (G6)."""
    table = json.loads(Path(table_path).read_text())
    base = absolute_spec(json.loads(p154.GATE_SPEC.read_text()), args.library_dir)
    ed = standard_ed("Fe")
    spec = copy.deepcopy(base)
    spec["title"] = "P156 G6 FNS Fe on the shipped table"
    spec["material"] = {"mass_g": 1.0, "basis": "wt_percent", "composition": {"FE": 100.0}}
    spec["damage"] = {"table": {"path": str(table_path), "sha256": sha256(table_path)},
                      "displacement_energy_eV": {"Fe": ed}}
    spec["options"] = dict(spec["options"], gas=True, outputs=["damage", "ledger", "certificate"])
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    spec_path, out = work / f"{label}.spec.json", work / f"{label}.out.json"
    spec_path.write_text(json.dumps(spec))
    result = p154.run_spec(cand, spec_path, out, args.timeout)
    covered, fracs, covered_fraction, atoms_per_g = fe_covered(table["targets"])
    rows = [table["targets"][f"Fe{a}"] for a in covered]
    if not rows:
        raise GateError("the table has no Fe isotope row")
    row = effective_row(rows, fracs)
    tb = [float(b) for b in table["boundaries_eV"]]
    case = p154.check_run_with_bounds(result, spec, tb, row, "Fe", ed, "displacement_arc_barn" in row, atoms_per_g)
    fractions, bad = [], []
    for k, step in enumerate(result["steps"]):
        value = p154.find_key(step, "covered_atom_fraction")
        fractions.append(value)
        if value is None or p154.rel(value, covered_fraction) > TOL_RUN:
            bad.append({"step": k, "covered_atom_fraction": value, "expected": covered_fraction})
    case["covered_atom_fraction"] = {"expected": covered_fraction, "steps": len(fractions), "bad": bad[:10],
                                     "pass": bool(fractions) and not bad}
    case["covered_fe_isotopes"] = covered
    case["pass"] = bool(case["pass"] and case["covered_atom_fraction"]["pass"])
    case["output_sha256"] = sha256(out)
    case["table_sha256"] = sha256(table_path)
    return case


def damage_blocks(result):
    return {"steps": [s.get("damage") for s in result.get("steps", [])],
            "ledger": result.get("ledger", {}).get("damage")}


def gate_g6(cand, work, args, table_path):
    work = Path(work) / "g6"
    work.mkdir(parents=True, exist_ok=True)
    data_dir = str(Path(args.data_dir).expanduser().resolve())
    cases = {}
    if not table_path:
        raise GateError("no shipped table: give --table or a --data-dir holding the catalog 1.2.0 table")

    # actinv new against the explicit spec
    new_path = work / "new.json"
    if new_path.exists():
        new_path.unlink()
    code, _o, err = sh([cand, "new", new_path, "--data-dir", data_dir], 300)
    if code != 0 or not new_path.exists():
        raise GateError(f"actinv new failed: {err.strip()[-300:]}")
    new_spec = json.loads(new_path.read_text())
    dmg = new_spec.get("damage") or {}
    outputs = new_spec.get("options", {}).get("outputs", [])
    case = {"damage_section": bool(dmg), "options_outputs": outputs,
            "spec_damage": dmg, "energies_in_new_spec": dmg.get("displacement_energy_eV")}
    table = json.loads(Path(table_path).read_text())
    composition = new_spec.get("material", {}).get("composition", {})
    elements = sorted({re.match(r"[A-Za-z]+", k).group(0).capitalize() for k in composition})
    energies = {el: table["displacement"]["displacement_energy_eV"][el] for el in elements
                if el in table["displacement"]["displacement_energy_eV"]}
    case["elements"], case["explicit_energies"] = elements, energies
    explicit = copy.deepcopy(new_spec)
    tpath = dmg.get("table", {}).get("path") if isinstance(dmg.get("table"), dict) else None
    tsha = dmg.get("table", {}).get("sha256") if isinstance(dmg.get("table"), dict) else None
    explicit["damage"] = dict(dmg, table={"path": str(tpath if tpath and Path(str(tpath)).is_file() else table_path),
                                          "sha256": tsha or sha256(table_path)}, displacement_energy_eV=energies)
    case["table_sha256_matches_new_spec"] = (tsha or sha256(table_path)) == sha256(explicit["damage"]["table"]["path"])
    results = {}
    for label, spec in (("new", new_spec), ("explicit", explicit)):
        sp, op = work / f"{label}.spec.json", work / f"{label}.out.json"
        sp.write_text(json.dumps(spec))
        results[label] = p154.run_spec(cand, sp, op, args.timeout)
    blocks, removed = {}, {}
    for label, result in results.items():
        blocks[label], removed[label] = strip_energy_source(damage_blocks(result), args.energy_source_key)
    case["energy_source_values"] = {k: sorted({str(v) for v in source_values(removed[k])}) for k in removed}
    case["blocks_equal_apart_from_energy_source"] = blocks["new"] == blocks["explicit"]
    case["differences"] = json_diff_paths(blocks["new"], blocks["explicit"])
    case["steps_compared"] = len(blocks["new"]["steps"])
    case["energy_source_field"] = {"new": [p for p, _v in removed["new"]][:6], "explicit": [p for p, _v in removed["explicit"]][:6],
                                   "new_is_table": bool(removed["new"]) and set(source_values(removed["new"])) == {"table"},
                                   "explicit_is_spec": bool(removed["explicit"])
                                   and set(source_values(removed["explicit"])) == {"spec"}}
    cert_new = results["new"].get("certificate", {}).get("damage")
    cert_explicit = results["explicit"].get("certificate", {}).get("damage")
    case["certificate_information"] = {"equal_apart_from_energy_source":
                                       strip_energy_source(cert_new, args.energy_source_key)[0]
                                       == strip_energy_source(cert_explicit, args.energy_source_key)[0]}
    case["every_step_has_damage_block"] = all(isinstance(r.get("steps"), list) and r["steps"]
                                              and all(isinstance(s.get("damage"), dict) for s in r["steps"])
                                              for r in results.values())
    case["pass"] = bool(case["damage_section"] and case["every_step_has_damage_block"] and case["steps_compared"] > 0
                        and case["blocks_equal_apart_from_energy_source"]
                        and case["energy_source_field"]["new_is_table"] and case["energy_source_field"]["explicit_is_spec"])
    cases["new_equals_explicit"] = case

    # FNS Fe on the shipped table
    try:
        cases["fns_fe_run"] = fns_run(cand, work, args, table_path)
    except (GateError, KeyError, ValueError, OSError, TypeError) as error:
        cases["fns_fe_run"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}

    # an energy that is not the table's is refused and writes nothing
    refused = copy.deepcopy(explicit)
    refused["damage"]["displacement_energy_eV"] = dict(energies, Fe=energies.get("Fe", standard_ed("Fe")) + 1.0)
    sp, op = work / "refused.spec.json", work / "refused.out.json"
    sp.write_text(json.dumps(refused))
    if op.exists():
        op.unlink()
    code, _o, err = sh([cand, "run", sp, op], args.timeout)
    cases["fe_41_refused"] = {"given": refused["damage"]["displacement_energy_eV"], "exit_code": code,
                              "output_written": op.exists(), "stderr_tail": err.strip()[-300:],
                              "pass": code != 0 and not op.exists()}

    # FISPACT-II deck with ALLDISPEN imports without --damage
    try:
        cases["alldispen_import"] = alldispen_import(cand, work, args, data_dir, table)
    except (GateError, KeyError, ValueError, OSError, TypeError) as error:
        cases["alldispen_import"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    return {"pass": all(c.get("pass") is True for c in cases.values()), "cases": cases,
            "assumptions": "A1, A3 of the module docstring"}


def fns_deck_with(directory, source_dir, energy_line):
    """A copy of the FNS Fe 5-minute deck and its small sibling files in `directory` with `energy_line` after GETDECAY."""
    source_dir = Path(source_dir)
    deck = sorted(source_dir.glob("*5min.i"))
    if not deck:
        raise GateError(f"no FNS 5-minute deck under {source_dir}")
    directory = Path(directory)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    for p in source_dir.iterdir():
        if p.is_file() and not p.suffix in (".pdf", ".gra", ".out") and p.stat().st_size < 5_000_000:
            shutil.copyfile(p, directory / p.name)
    path = directory / deck[0].name
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("GETDECAY"):
            lines.insert(i + 1, energy_line)
            break
    else:
        raise GateError("the FNS deck has no GETDECAY line")
    path.write_text("\n".join(lines) + "\n")
    return path


def report_path(out):
    out = str(out)
    return out[:-5] + ".import.json" if out.endswith(".json") else out + ".import.json"


def import_deck(cand, deck, out, env, timeout=300):
    out = Path(out)
    for p in (out, Path(report_path(out))):
        if p.exists():
            p.unlink()
    code, so, se = sh([cand, "import-fispact", deck, out], timeout, env)
    spec = json.loads(out.read_text()) if out.is_file() else None
    rep = Path(report_path(out))
    report = json.loads(rep.read_text()) if rep.is_file() else None
    return {"exit": code, "stderr": se.strip()[-300:], "spec": spec, "report": report}


def alldispen_import(cand, work, args, data_dir, table):
    ed = table["displacement"]["displacement_energy_eV"].get("Fe", standard_ed("Fe"))
    env = dict(os.environ, ACTINV_DATA_DIR=data_dir)
    for name in ("ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE"):
        env.pop(name, None)
    src = Path(args.fns).expanduser() / "Fe"
    cases = {}
    for label, energy in (("standard", ed), ("other_energy", ed + 17.0)):
        deck = fns_deck_with(work / "alldispen" / label, src, f"ALLDISPEN {energy!r}")
        imp = import_deck(cand, deck, work / "alldispen" / f"{label}.json", env)
        entries = [e for e in (imp["report"] or {}).get("entries", []) if str(e.get("keyword", "")).upper() == "ALLDISPEN"]
        statuses = [e.get("status") for e in entries]
        spec_dmg = (imp["spec"] or {}).get("damage")
        if label == "standard":
            ok = imp["exit"] == 0 and bool(statuses) and all(s == "mapped" for s in statuses) and bool(spec_dmg) \
                and isinstance(spec_dmg.get("table"), dict)
            energies = (spec_dmg or {}).get("displacement_energy_eV")
            if energies:
                ok = ok and all(abs(v - ed) < 1e-12 for k, v in energies.items() if k.upper() == "FE")
        else:
            ok = bool(statuses) and any(s == "unsupported" for s in statuses)
        cases[label] = {"energy_eV": energy, "exit": imp["exit"], "alldispen_statuses": statuses,
                        "spec_damage": spec_dmg, "stderr": imp["stderr"], "pass": bool(ok)}
    return {"pass": all(c["pass"] for c in cases.values()), "cases": cases,
            "deck": "FNS Fe 5-minute deck with the keyword inserted after GETDECAY, ACTINV_DATA_DIR set, no --damage"}


# ------------------------------------------------------------------------------ G7


def table_path_in(data_dir, catalog):
    art = next((a for a in catalog["artifacts"] if a["role"] == CATALOG_ROLE), None)
    if art is None:
        return None, None
    root = Path(data_dir).expanduser()
    for cand in (root / f"v{catalog['catalog_version']}" / art["path"], root / art["path"]):
        if cand.is_file():
            return cand, art
    return None, art


def gate_g7(cand, work, args):
    work = Path(work) / "g7"
    work.mkdir(parents=True, exist_ok=True)
    cases = {}
    old = json.loads(CATALOG_OLD.read_text())
    code, out, err = sh([cand, "data", "manifest"], 120)
    if code != 0:
        raise GateError(f"data manifest failed: {err.strip()[-300:]}")
    start = out.find("{")
    manifest = json.loads(out[start:])
    repo_catalog = json.loads(Path(args.catalog).read_text()) if Path(args.catalog).is_file() else None
    case = catalog_checks(old, manifest)
    case["manifest_equals_repository_catalog"] = repo_catalog == manifest
    case["pass"] = case["pass"] and case["manifest_equals_repository_catalog"]
    code, lout, lerr = sh([cand, "data", "list"], 120)
    case["data_list"] = {"exit": code, "mentions_table": CATALOG_ID in lout, "stderr": lerr.strip()[-200:]}
    case["pass"] = case["pass"] and code == 0
    cases["catalog_1_2_0"] = case
    table_art = next((a for a in manifest.get("artifacts", []) if a.get("role") == CATALOG_ROLE), None)

    if args.staged:
        staged = Path(args.staged).expanduser()
        sums, sizes = parse_sums((staged / "SHA256SUMS").read_text()), parse_sizes((staged / "SIZES").read_text())
        problems, listed = [], 0
        for name, digest in sums.items():
            p = staged / name
            listed += 1
            if not p.is_file():
                problems.append(f"{name}: listed in SHA256SUMS but absent")
                continue
            if sha256(p) != digest:
                problems.append(f"{name}: SHA-256 differs from SHA256SUMS")
            if name not in sizes:
                problems.append(f"{name}: not in SIZES")
            elif sizes[name] != p.stat().st_size:
                problems.append(f"{name}: size {p.stat().st_size} differs from SIZES {sizes[name]}")
        for name in sorted(set(sizes) - set(sums)):
            problems.append(f"{name}: in SIZES but not SHA256SUMS")
        unlisted = sorted(p.name for p in staged.iterdir() if p.is_file() and p.name not in sums
                          and p.name not in ("SHA256SUMS", "SIZES"))
        if unlisted:
            problems.append(f"files not listed: {unlisted}")
        table_ok = False
        if table_art is not None:
            name = Path(table_art["source"]["url"]).name
            table_ok = sums.get(name) == table_art["sha256"] and sizes.get(name) == table_art["bytes"]
            if not table_ok:
                problems.append(f"table asset {name} does not match the catalog bytes / sha256")
        cases["staged_assets"] = {"dir": str(staged), "files": listed, "problems": problems,
                                  "table_matches_catalog": table_ok, "pass": not problems and listed > 0}
    if args.fetch_check:
        cases["fetch_downloads_only_table"] = fetch_check(cand, work, args, old, manifest, table_art)
    if args.published:
        cases["published"] = published_check(cand, work, args, manifest)
    return {"pass": all(c.get("pass") is True for c in cases.values()), "cases": cases}


def stat_key(path):
    st = os.stat(path)
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def fetch_check(cand, work, args, old, new, table_art):
    """A fetch of tendl-2025-neutron into a directory seeded (copies) with the v1.1.0 files downloads only the table."""
    out = work / "fetch_seeded"
    if out.exists():
        shutil.rmtree(out)
    bundle = next(b for b in old["bundles"] if b["id"] == "tendl-2025-neutron")
    by_id = {a["id"]: a for a in old["artifacts"]}
    seeded, missing = {}, []
    for aid in bundle["artifacts"]:
        art = by_id[aid]
        src = Path(args.library_dir) / art["path"]
        if not src.is_file():
            missing.append(art["path"])
            continue
        dst = out / "v1.1.0" / art["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        seeded[art["path"]] = (stat_key(dst), art["sha256"])
    env = dict(os.environ)
    for item in args.fetch_env or []:
        key, _, value = item.partition("=")
        env[key] = value
    code, so, se, wall, _peak = timed([cand, "data", "fetch", "tendl-2025-neutron", "--output", out], args.timeout, env)
    text = so + "\n" + se
    new_dir = out / f"v{new['catalog_version']}"
    by_new = {a["id"]: a for a in new["artifacts"]}
    new_bundle = next(b for b in new["bundles"] if b["id"] == "tendl-2025-neutron")
    arts = [by_new[a] for a in new_bundle["artifacts"]]
    seeded_shas = {sha for _k, sha in seeded.values()}
    expected_new = [a["id"] for a in arts if a["sha256"] not in seeded_shas and a["role"] != "notice"]
    files_ok, untouched = [], []
    for a in arts:
        p = new_dir / a["path"]
        files_ok.append({"id": a["id"], "present": p.is_file(), "sha256_ok": p.is_file() and sha256(p) == a["sha256"]})
    for path, (key, _sha) in seeded.items():
        p = out / "v1.1.0" / path
        untouched.append({"path": path, "untouched": p.is_file() and stat_key(p) == key})
    case = {"exit": code, "wall_s": wall, "seeded": sorted(seeded), "seed_missing": missing,
            "expected_downloads_by_sha": expected_new, "stdout_tail": so.strip()[-800:], "stderr_tail": se.strip()[-400:],
            "v110_files_untouched": all(u["untouched"] for u in untouched) and bool(untouched),
            "v120_files_all_present_and_verified": all(f["present"] and f["sha256_ok"] for f in files_ok)}
    if args.network_log:
        urls = [ln.strip() for ln in Path(args.network_log).expanduser().read_text().splitlines() if ln.strip()]
        want = table_art["source"]["url"] if table_art else None
        case["observed_by"] = "network log"
        case["downloads_only_table"] = bool(urls) and all(u == want for u in urls)
        case["urls"] = urls[:10]
    else:
        reported = downloaded_in_output(text, arts)
        case["downloaded_per_output"] = reported
        case["observed_by"] = "fetch output" if reported else "file identity (output names no download)"
        case["downloads_only_table"] = (set(reported) <= {CATALOG_ID} if reported else expected_new == [CATALOG_ID])
    case["pass"] = bool(code == 0 and case["v110_files_untouched"] and case["v120_files_all_present_and_verified"]
                        and case["downloads_only_table"] and expected_new == [CATALOG_ID])
    return case


def published_check(cand, work, args, manifest):
    out = work / "published"
    if out.exists():
        shutil.rmtree(out)
    case = {}
    code, so, se, wall, _p = timed([cand, "data", "fetch", "--output", out], args.timeout)
    case["fetch"] = {"exit": code, "wall_s": wall, "stderr_tail": se.strip()[-300:]}
    code2, so2, se2 = sh([cand, "data", "verify", "--output", out], args.timeout)
    case["verify"] = {"exit": code2, "stderr_tail": se2.strip()[-300:]}
    path, art = table_path_in(out, manifest)
    case["table"] = str(path) if path else None
    case["pass"] = False
    if code == 0 and code2 == 0 and path is not None:
        ns = copy.copy(args)
        ns.library_dir = str(out / f"v{manifest['catalog_version']}")
        case["fns_fe_run"] = fns_run(cand, work / "published_run", ns, path, "published")
        case["pass"] = case["fns_fe_run"]["pass"]
    return case


# ------------------------------------------------------------------------------ self-check


def synthetic_tape():
    """A two-material ENDF decay tape in the real column layout (MF=8 MT=457), plus noise lines of other sections."""
    def fld(x):
        mant, exp = f"{x:.6e}".split("e")
        e = int(exp)
        return f"{mant}{'+' if e >= 0 else '-'}{abs(e)}".rjust(11)

    def ifld(n):
        return str(n).rjust(11)

    def line(fields, mat, mf, mt, seq):
        return "".join(fields).ljust(66) + f"{mat:4d}{mf:2d}{mt:3d}{seq:5d}"
    out = [line([fld(26056.0), fld(55.4), ifld(0), ifld(0), ifld(1), ifld(0)], 2631, 8, 457, 1),     # Fe56 stable
           line([fld(0.0), fld(0.0), ifld(0), ifld(0), ifld(6), ifld(0)], 2631, 8, 457, 2),
           line([fld(1.0)] * 6, 2631, 8, 457, 3),
           line([fld(26055.0), fld(54.5), ifld(0), ifld(0), ifld(0), ifld(1)], 2630, 3, 1, 1),      # not MF=8
           line([fld(27060.0), fld(59.5), ifld(0), ifld(0), ifld(0), ifld(1)], 2725, 8, 457, 1),     # Co60 5.27 y
           line([fld(1.66e8), fld(0.5), ifld(0), ifld(0), ifld(6), ifld(0)], 2725, 8, 457, 2),
           line([fld(27060.0), fld(59.5), ifld(0), ifld(1), ifld(0), ifld(1)], 2726, 8, 457, 1),     # Co60m 10.5 min
           line([fld(628.0), fld(0.5), ifld(0), ifld(0), ifld(6), ifld(0)], 2726, 8, 457, 2),
           line([fld(25054.0), fld(53.5), ifld(0), ifld(0), ifld(0), ifld(1)], 2540, 8, 457, 1),     # Mn54 312 d
           line([fld(2.696e7), fld(0.5), ifld(0), ifld(0), ifld(6), ifld(0)], 2540, 8, 457, 2),
           line([fld(73182.0), fld(180.0), ifld(0), ifld(0), ifld(0), ifld(1)], 7440, 8, 457, 1),    # Ta182 114 d
           line([fld(9.9e6), fld(0.5), ifld(0), ifld(0), ifld(6), ifld(0)], 7440, 8, 457, 2),
           line([fld(73181.0), fld(180.0), ifld(0), ifld(0), ifld(1), ifld(0)], 7430, 8, 457, 1),    # Ta181 stable
           line([fld(0.0), fld(0.0), ifld(0), ifld(0), ifld(6), ifld(0)], 7430, 8, 457, 2)]
    return out


def self_check():
    failures = []

    def check(name, cond, detail=""):
        print(f"  {'ok  ' if cond else 'FAIL'} {name}" + ("" if cond else f"  {detail}"))
        if not cond:
            failures.append(name)

    print("P156 checker self-check")
    # --- ENDF numbers and the decay-record parser on a synthetic tape
    check("endf_number", endf_number("1.000000+0") == 1.0 and endf_number("-2.5-3") == -2.5e-3 and endf_number("   ") == 0.0
          and endf_number("3.15576+7") == 3.15576e7)
    recs = parse_decay_lines(synthetic_tape())
    check("parser: MF=8 MT=457 records only", sorted(k for k in recs if k != "_meta") ==
          [(25, 54, 0), (26, 56, 0), (27, 60, 0), (27, 60, 1), (73, 181, 0), (73, 182, 0)],
          str(sorted(k for k in recs if k != "_meta")))
    check("parser: stable NST and half-lives", recs[(26, 56, 0)]["nst"] == 1 and recs[(27, 60, 0)]["nst"] == 0
          and abs(recs[(27, 60, 0)]["t_half_s"] - 1.66e8) < 1 and recs[(27, 60, 1)]["t_half_s"] == 628.0
          and recs[(73, 182, 0)]["t_half_s"] == 9.9e6)
    check("parser: LISO from columns 34-44", (27, 60, 1) in recs and recs[(27, 60, 1)]["za"] == 27060)
    # --- file names
    check("tendl names", parse_tendl_name("n-Fe056.tendl") == ("Fe", 56, 0) and parse_tendl_name("n-Co060m.tendl") == ("Co", 60, 1)
          and parse_tendl_name("n-Ag116n.tendl") == ("Ag", 116, 2) and parse_tendl_name("n-Zz001.tendl") is None
          and parse_tendl_name("n-W184.tendl") == ("W", 184, 0) and nuclide_name("Ta", 180, 1) == "Ta180m1"
          and nuclide_name("Fe", 56, 0) == "Fe56")
    # --- selection: stable or T1/2 >= 3.15576e7 s, primary record before fallback, no record excluded
    primary = {k: v for k, v in recs.items()}
    fallback = {(26, 55, 0): {"nst": 0, "t_half_s": 8.6e7, "mat": "x", "za": 26055},      # Fe55 2.7 y, fallback only
                (27, 60, 0): {"nst": 1, "t_half_s": 0.0, "mat": "x", "za": 27060},        # primary must win
                (73, 182, 0): {"nst": 0, "t_half_s": 9.9e6, "mat": "x", "za": 73182}}
    names = ["n-Fe056.tendl", "n-Fe055.tendl", "n-Co060.tendl", "n-Co060m.tendl", "n-Mn054.tendl", "n-Ta182.tendl",
             "n-Ta181.tendl", "n-Xe135.tendl", "n-Mn055.tendl"]
    sel = select_targets(names, primary, fallback)
    chosen = {r["file"]: r for r in sel["selected"]}
    check("selection: stable and long-lived", set(chosen) == {"n-Fe056.tendl", "n-Fe055.tendl", "n-Co060.tendl", "n-Ta181.tendl"},
          str(sorted(chosen)))
    check("selection: fallback used only without a primary record", chosen["n-Fe055.tendl"]["source"] == "fallback"
          and chosen["n-Co060.tendl"]["source"] == "primary")
    reasons = {e["file"]: e["reason"] for e in sel["excluded"]}
    check("selection: exclusions reasoned", "no MF=8" in reasons["n-Xe135.tendl"] and "no MF=8" in reasons["n-Mn055.tendl"]
          and "below one year" in reasons["n-Co060m.tendl"] and "below one year" in reasons["n-Mn054.tendl"]
          and "below one year" in reasons["n-Ta182.tendl"])
    check("selection: the year threshold is inclusive",
          select_targets(["n-Fe055.tendl"], {(26, 55, 0): {"nst": 0, "t_half_s": YEAR_S, "mat": "", "za": 0}}, {})["selected"]
          and not select_targets(["n-Fe055.tendl"], {(26, 55, 0): {"nst": 0, "t_half_s": YEAR_S * (1 - 1e-12), "mat": "", "za": 0}},
                                 {})["selected"])
    # --- standard energy table
    exp = {"Be": 31, "C": 31, "Mg": 25, "Al": 27, "Si": 25, "Ca": 40, "Ti": 40, "V": 40, "Cr": 40, "Mn": 40, "Fe": 40,
           "Co": 40, "Ni": 40, "Cu": 40, "Zr": 40, "Nb": 40, "Mo": 60, "Ag": 60, "Ta": 90, "W": 90, "Au": 30, "Pb": 25}
    check("standard E_d table", all(standard_ed(k) == v for k, v in exp.items()) and standard_ed("Li") == 25.0
          and standard_ed("U") == 25.0 and standard_ed("Zn") == 25.0 and set(STANDARD_ED) == set(exp), str(STANDARD_ED))
    check("G4 targets use the standard energies", {el: standard_ed(el) for (_f, el, _s) in G4_TARGETS.values()} ==
          {"V": 40, "Ti": 40, "Cu": 40, "Ta": 90, "Mo": 60, "Nb": 40, "Mn": 40, "Co": 40, "Si": 25, "Al": 27, "Be": 31, "Li": 25})
    check("symbol table", len(SYMBOLS) == 118 and Z_OF["Fe"] == 26 and Z_OF["W"] == 74 and Z_OF["U"] == 92)
    # --- row sums and canonical comparison
    n = 709
    good = {k: [1.0 + 0.001 * g for g in range(n)] for k in ROW_REQUIRED}
    good["elastic"] = [0.5] * n
    good["inelastic"] = [0.25] * n
    good["disappearance"] = [0.25] * n
    good["damage_energy_barn_eV"] = [1.0] * n
    check("row sound", row_problems(good) == [])
    bad_sum = copy.deepcopy(good)
    bad_sum["damage_energy_barn_eV"][3] = 1.0 * (1 + 1e-11)
    check("row sum tolerance 1e-12", row_problems(bad_sum) != [] and
          row_problems(dict(good, damage_energy_barn_eV=[1.0 * (1 + 5e-13)] + [1.0] * (n - 1))) == [])
    neg = copy.deepcopy(good)
    neg["elastic"][0] = -1e-30
    nan = copy.deepcopy(good)
    nan["uncovered_xs_barn"][5] = float("nan")
    short = dict(good, inelastic=[0.0] * (n - 1))
    check("row negative / non-finite / short", row_problems(neg) != [] and row_problems(nan) != [] and row_problems(short) != [])
    check("row zero group", row_problems(dict(good, damage_energy_barn_eV=[0.0] + [1.0] * (n - 1), elastic=[0.0] + [0.5] * (n - 1),
                                              inelastic=[0.0] + [0.25] * (n - 1), disappearance=[0.0] + [0.25] * (n - 1))) == [])
    a = {"x": [1.0, 2.0], "y": 1}
    b = {"y": 1, "x": [1.0, 2.0]}
    check("canonical row comparison", canon(a) == canon(b) and canon(a) != canon({"x": [1.0, 2.0000000000000004], "y": 1}))
    cmp = compare_rows({"A": a, "B": a}, {"A": b, "B": {"x": [1.0, 3.0], "y": 1}, "C": a}, ["A", "B", "C"])
    check("compare_rows", cmp["identical"] == ["A"] and cmp["different"] == ["B"] and cmp["only_in_rebuild"] == ["C"]
          and not cmp["pass"] and compare_rows({"A": a}, {"A": b}, ["A"])["pass"])
    # --- files lists and uncovered entries
    check("normalize_files", normalize_files([{"path": "n-Fe056.tendl", "sha256": "ab"}, {"name": "/x/n-Be009.tendl", "sha256": "cd"}])
          == {"n-Fe056.tendl": "ab", "n-Be009.tendl": "cd"} and normalize_files({"a": "1"}) == {"a": "1"})
    unc = [{"nuclide": "Ta180m1", "reason": "no MF=3 section"}, "Fe55 skipped: no evaluation", {"file": "n-Be009.tendl"}]
    check("uncovered lookup", find_uncovered_entry(unc, "Ta180m1", "n-Ta180m.tendl")["has_reason"]
          and find_uncovered_entry(unc, "Fe55", "n-Fe055.tendl")["has_reason"]
          and find_uncovered_entry(unc, "Be9", "n-Be009.tendl")["has_reason"] is False
          and find_uncovered_entry(unc, "Ta180", "n-Ta180.tendl") is None)
    # --- argument construction
    check("item-6 command", ITEM6_ARGS == ["--method", "kinematic", "--projectile", "neutron", "--groups", "fispact-709",
                                           "--continuum-recoil", "evaluated", "--continue-on-error", "true"]
          and ed_args({"W": 70.0, "Fe": 40.0}) == ["--displacement-energy", "W=70", "--displacement-energy", "Fe=40"]
          and ed_args({"Ni": 39.5}) == ["--displacement-energy", "Ni=39.5"])
    check("P154 build arguments are explicit and unchanged", p154.build_args("Fe56", p154.BUILD_ED, "per-photon", None, "evaluated") ==
          ["--method", "kinematic", "--groups", "fispact-709", "--projectile", "neutron", "--capture-photons", "per-photon",
           "--displacement-energy", "Fe=40", "--continuum-recoil", "evaluated"])
    # wrapping restores the P154 globals
    before = (dict(p154.TARGETS), dict(p154.BUILD_ED), p154.build_table)
    targets, ed_map = g4_target_table()
    with p154_targets(targets, ed_map):
        inside = (set(p154.TARGETS), p154.BUILD_ED["Ta"], p154.build_args("Ta181", ed_map, "per-photon", None, "kinematic"))
        limit_modes(False)
        try:
            p154.build_table("x", "Be9", Path("."), ed_map, "per-photon", None, 1, False, "evaluated")
        except Exception as error:      # the first call reaches the original (which fails on the missing binary)
            first = type(error).__name__
        try:
            p154.build_table("x", "Be9", Path("."), ed_map, "one-photon", None, 1, False, "kinematic")
            second = "built"
        except GateError as error:
            second = "skipped" if "skipped" in str(error) else "other"
    check("p154 globals rebound and restored", inside[0] == set(G4_TARGETS) and inside[1] == 90.0
          and "Ta=90" in inside[2] and (dict(p154.TARGETS), dict(p154.BUILD_ED), p154.build_table) == before
          and second == "skipped" and first != "GateError", f"{inside} {first} {second}")
    check("targets are 12 and pinned", len(G4_TARGETS) == 12 and all(len(s) == 64 for (_f, _e, s) in G4_TARGETS.values())
          and all(parse_tendl_name(f) is not None for (f, _e, _s) in G4_TARGETS.values()))
    # --- energy-source stripping
    result = {"ledger": {"damage": {"displacement_energy_source": {"Fe": "table"}, "table_sha256": "x"}},
              "steps": [{"damage": {"dpa": 1.0}}]}
    clean, found = strip_energy_source(result)
    check("energy-source field stripped", len(found) == 1 and "displacement_energy_source" not in json.dumps(clean)
          and clean["steps"][0]["damage"] == {"dpa": 1.0} and set(source_values(found)) == {"table"})
    spec_side, found2 = strip_energy_source({"ledger": {"damage": {"displacement_energy_source": {"Fe": "spec"}, "table_sha256": "x"}},
                                             "steps": [{"damage": {"dpa": 1.0}}]})
    check("blocks equal apart from the source", clean == spec_side and set(source_values(found2)) == {"spec"}
          and json_diff_paths(clean, spec_side) == [] and json_diff_paths({"a": [1, 2]}, {"a": [1, 3]}) == ["/a[1]: 2 != 3"])
    # --- catalog checks on synthetic 1.2.0 built from the real 1.1.0
    old = json.loads(CATALOG_OLD.read_text())
    new = copy.deepcopy(old)
    new["catalog_version"] = "1.2.0"
    new["release_url"] = old["release_url"].replace("data-v1.1.0", "data-v1.2.0")
    new["notice"] = "ACTINV-DATA-NOTICE-v1.2.0.md"
    new["artifacts"].append({"id": CATALOG_ID, "role": CATALOG_ROLE, "path": "damage/tendl-2025-neutron-damage.json",
                             "bytes": 1234, "sha256": "a" * 64, "licence": "CC-BY-4.0",
                             "source": {"url": "https://github.com/AvilaLabs/ACTINV/releases/download/data-v1.2.0/x.json",
                                        "bytes": 1234, "sha256": "a" * 64}})
    for b in new["bundles"]:
        if b["id"] in NEUTRON_BUNDLES:
            b["artifacts"].append(CATALOG_ID)
    good_cat = catalog_checks(old, new, repo_notice_dir=None)
    check("catalog checks accept a sound 1.2.0", good_cat["pass"] and good_cat["v110_artifacts_unchanged"] == len(old["artifacts"])
          and good_cat["added_artifacts"] == [CATALOG_ID], str(good_cat["problems"]))
    changed = copy.deepcopy(new)
    changed["artifacts"][0]["bytes"] += 1
    missing_bundle = copy.deepcopy(new)
    missing_bundle["bundles"][0]["artifacts"].remove(CATALOG_ID)
    proton = copy.deepcopy(new)
    next(b for b in proton["bundles"] if b["projectile"] == "proton")["artifacts"].append(CATALOG_ID)
    check("catalog checks reject changes", not catalog_checks(old, changed, None)["pass"]
          and not catalog_checks(old, missing_bundle, None)["pass"] and not catalog_checks(old, proton, None)["pass"]
          and not catalog_checks(old, old, None)["pass"])
    superseding = copy.deepcopy(new)
    superseding["artifacts"].append({"id": "notice-new", "role": "notice", "path": "N.md", "bytes": 1, "sha256": "b" * 64})
    dropped_data = copy.deepcopy(superseding)
    for b in superseding["bundles"] + dropped_data["bundles"]:
        if b["id"] in NEUTRON_BUNDLES:
            b["artifacts"] = [a for a in b["artifacts"] if not a.startswith("actinv-data-notice")] + ["notice-new"]
    first_neutron = next(b for b in dropped_data["bundles"] if b["id"] in NEUTRON_BUNDLES)
    first_neutron["artifacts"] = [a for a in first_neutron["artifacts"] if "decay" not in a]
    sup = catalog_checks(old, superseding, None)
    check("a new notice may supersede an old one in a bundle; data may not be dropped",
          sup["pass"] and set(sup["notices_superseded"]) == set(NEUTRON_BUNDLES)
          and not catalog_checks(old, dropped_data, None)["pass"], str(sup["problems"]))
    # --- Amendment D
    leg = {"kind": "legendre", "energies": [1.0e7, 2.0e7], "data": [[0.2], [0.3]], "ranges": [(2, 2)]}
    tab = {"kind": "tabulated", "energies": [2.0e7, 3.0e7],
           "data": [{"x": [-1.0, 1.0], "y": [0.1, 0.9], "ranges": [(2, 2)]}] * 2, "ranges": [(2, 2)]}
    mf4 = {"za": 26056, "awr": 55.45, "lct": 2, "li": 0, "ltt": 3, "parts": [leg, tab]}
    w0, w1 = _P154_W_TABLE(mf4, 40.0), w_table_d1(mf4, 40.0)
    left_d1 = p154.interp_value({"x": w1["x"], "y": w1["y"], "ranges": w1["ranges"]}, 2.0e7 * (1 - 1e-6))
    left_exact = p154.elastic_w(p154.legendre_pdf([0.3]), 2.0e7, 55.45, 26, 40.0)
    right = p154.elastic_w(p154.tabulated_pdf(tab["data"][0])[:2], 2.0e7, 55.45, 26, 40.0)
    check("D1: W steps at the LTT=3 switch (left Legendre, right tabulated)",
          abs(left_d1 / left_exact - 1) < 1e-5 and abs(w1["y"][w1["x"].index(2.0e7)] / right - 1) < 1e-12
          and abs(left_exact / right - 1) > 1e-2 and len(w1["x"]) == len(w0["x"]) + 1
          and w_table_d1(dict(mf4, ltt=1, parts=[leg]), 40.0) == _P154_W_TABLE(dict(mf4, ltt=1, parts=[leg]), 40.0),
          f"{left_d1} {left_exact} {right}")
    iso = p154.legendre_pdf([])
    far = (heatr_level_w(iso, 5.0e6, 55.45, 26, 40.0, 9.0e5), p154.inelastic_w(iso, 5.0e6, 55.45, 26, 40.0, 9.0e5))
    near = (heatr_level_w(iso, 8900.0, 179.4, 73, 90.0, 6271.9), p154.inelastic_w(iso, 8900.0, 179.4, 73, 90.0, 6271.9))
    check("D2: 64-point rule equals the exact W far from the damage threshold, differs where the recoil straddles it",
          abs(far[0] / far[1] - 1) < 1e-6 and near[1] > 0.0 and abs(near[0] / near[1] - 1) > 1e-4, f"{far} {near}")
    vac = {"vacuous": True, "bad": [], "pass": False, "with_uncovered_reported": {"n": 40}, "evaluated_groups": 2}
    okc = {"vacuous": False, "bad": [], "pass": True, "with_uncovered_reported": {"n": 0}}
    pre_vac = {"vacuous": True, "bad": [], "pass": False, "set_size": 3}
    pre_bad = {"vacuous": False, "bad": [{"group": 1}], "pass": False, "set_size": 40}
    d3a = apply_d3({"inelastic_vs_446": dict(vac), "disappearance_vs_447": dict(okc), "b2_precondition": dict(pre_vac),
                    "b1_precondition": dict(okc), "pass": False})
    d3b = apply_d3({"inelastic_vs_446": dict(vac), "disappearance_vs_447": dict(okc), "b2_precondition": dict(pre_bad),
                    "b1_precondition": dict(okc), "pass": False})
    d3c = apply_d3({"inelastic_vs_446": dict(vac, with_uncovered_reported={"n": 9}), "disappearance_vs_447": dict(okc),
                    "b2_precondition": dict(okc), "b1_precondition": dict(okc), "pass": False})
    d3d = apply_d3({"inelastic_vs_446": dict(vac, bad=[{"group": 0}]), "disappearance_vs_447": dict(okc),
                    "b2_precondition": dict(okc), "b1_precondition": dict(okc), "pass": False})
    check("D3: uncovered-vacuous comparisons reported; bad groups and too few uncovered groups still fail",
          d3a["pass"] and set(d3a["amendment_d3"]["reported_not_gating"]) == {"inelastic_vs_446", "b2_precondition"}
          and not d3b["pass"] and not d3c["pass"] and not d3d["pass"])
    with amendment_d_references():
        inside_d = (p154.w_table is w_table_d1, p154.level_w_tables is level_w_tables_d2, p154.B_VERSION)
    check("Amendment D rebinding restored", inside_d[0] and inside_d[1] and inside_d[2] != _P154_B_VERSION
          and p154.w_table is _P154_W_TABLE and p154.level_w_tables is _P154_LEVEL_W_TABLES
          and p154.B_VERSION == _P154_B_VERSION)
    # --- staging files, build logs, fetch output
    check("SHA256SUMS / SIZES parsing", parse_sums(f"{'a' * 64}  x.json\n{'B' * 64} *y.npz\njunk\n") == {"x.json": "a" * 64, "y.npz": "b" * 64}
          and parse_sizes("x.json 12\n34 y.npz\nbad line here\n") == {"x.json": 12, "y.npz": 34})
    log = "\tElapsed (wall clock) time (h:mm:ss or m:ss): 1:02:03.5\n\tMaximum resident set size (kbytes): 384440\n"
    check("time -v parsing", parse_time_log(log) == {"wall_s": 3723.5, "peak_rss_kB": 384440}
          and parse_time_log("") == {"wall_s": None, "peak_rss_kB": None})
    arts = [{"id": "a-lib", "path": "activation/a.npz"}, {"id": CATALOG_ID, "path": "damage/t.json"}]
    check("download detection", downloaded_in_output("downloading damage/t.json\nreused a.npz from v1.1.0\n"
                                                     "downloaded a-lib\ncopied a-lib (download skipped)", arts) == ["a-lib", CATALOG_ID]
          and downloaded_in_output("reused activation/a.npz\ncopying a.npz", arts) == [])
    # --- subset sampling is seeded and sorted
    pool = [f"n-X{i:03d}.tendl" for i in range(100)]
    s1 = sorted(random.Random(SEED).sample(pool, SUBSET))
    check("seeded subset", s1 == sorted(random.Random(SEED).sample(pool, SUBSET)) and len(set(s1)) == SUBSET)
    # --- covered Fe isotopes
    covered, fracs, frac, atoms = fe_covered({f"Fe{a}": {} for a in (54, 56, 57, 58)})
    check("covered Fe fraction", covered == [54, 56, 57, 58] and abs(frac - 1.0) < 1e-12 and atoms > 1e22)
    covered2, _f, frac2, _a = fe_covered({"Fe56": {}})
    check("covered Fe fraction of Fe56 only", covered2 == [56] and abs(frac2 - 0.91754 / 1.0) < 1e-3)
    er = effective_row([{"damage_energy_barn_eV": [1.0, 2.0]}, {"damage_energy_barn_eV": [3.0, 4.0]}], [1.0, 3.0])
    check("effective row", er == {"damage_energy_barn_eV": [2.5, 3.5]})
    # --- Amendment B: failed entries, structural rule, clip records
    sel_rows = [{"file": "n-Fe054.tendl", "nuclide": "Fe54", "stable": True}, {"file": "n-Fe056.tendl", "nuclide": "Fe56", "stable": True},
                {"file": "n-Xe136.tendl", "nuclide": "Xe136", "stable": True}, {"file": "n-Co060.tendl", "nuclide": "Co60", "stable": False},
                {"file": "n-Ta180m.tendl", "nuclide": "Ta180m1", "stable": False}]
    clip = {"mt": 2, "points": 3, "min_barn": -1e-3, "min_energy_eV": 3493.9, "low_eV": 3490.0, "high_eV": 3497.0}
    rws = {"Fe56": {"channels": {"zero_k_clipped": [clip, dict(clip, mt=102)]}}, "Ta180m1": {"channels": {}}}
    rep_ok = failure_report(sel_rows, rws, {"n-Xe136.tendl": {"file": "n-Xe136.tendl", "error": "x"},
                                            "n-Co060.tendl": {"file": "n-Co060.tendl", "error": "y"}})
    check("B2: failed entries counted, clips counted", rep_ok["failed_count"] == 2 and rep_ok["clipped_count"] == 1
          and rep_ok["clipped_records"] == 2 and rep_ok["failed_stable_structural"] == []
          and rep_ok["failed"][0]["natural_abundance_percent"] is None
          and rep_ok["clipped"][0]["natural_abundance_percent"] == 91.754, str(rep_ok))
    rep_bad = failure_report(sel_rows, rws, {"n-Fe054.tendl": {"file": "n-Fe054.tendl", "error": "negative"}})
    check("B2: stable structural failure is flagged", rep_bad["failed_stable_structural"] == ["Fe54"]
          and rep_bad["failed"][0]["natural_abundance_percent"] == 5.845)
    check("B2: unstable structural or non-structural failures are not", failure_report(
        sel_rows, rws, {"n-Co060.tendl": {"error": "y"}, "n-Ta180m.tendl": {"error": "z"}})["failed_stable_structural"] == [])
    check("B2: outside-selection, with-row and error-less entries reported", failure_report(
        sel_rows, rws, {"n-Fe056.tendl": {"error": "q"}, "n-Zz.tendl": {"error": "r"}, "n-Xe136.tendl": {}}) ["failed_entries_outside_selection"]
        == ["n-Zz.tendl"] and failure_report(sel_rows, rws, {"n-Fe056.tendl": {"error": "q"}})["failed_with_row"] == ["Fe56"]
        and failure_report(sel_rows, rws, {"n-Xe136.tendl": {}})["failed_without_error"] == ["n-Xe136.tendl"])
    check("failed_map / clipped_records", list(failed_map({"failed": [{"file": "/a/n-X.tendl", "sha256": "s", "error": "e"}]})) == ["n-X.tendl"]
          and clipped_records({"channels": {"zero_k_clipped": [clip]}}) == [clip] and clipped_records({}) == [])
    check("abundance table sums", all(abs(sum(v for k, v in NATURAL_ABUNDANCE_PERCENT.items() if element_of_nuclide(k) == el) - 100.0) < 0.05
                                      for el in STRUCTURAL_ELEMENTS))
    # --- G0 on the present protocol, and the example spec made absolute
    check("G0 hash line (protocol)", gate_g0()["protocol"]["in_protocol_hash_txt"] is True and "amendment_a" in gate_g0())
    base = [1.0 + 0.01 * g for g in range(50)] + [1e-9]
    near = [v * (1 + 9e-6) for v in base]
    nrt_a = [v * 1.2 for v in base]
    ok_cmp = arc_nrt_comparison(near, base, nrt_a, base)
    check("A1: arc 9e-6 apart and NRT 20% apart passes", ok_cmp["pass"] and 8e-6 < ok_cmp["largest_arc_rel_diff"] < 1e-5
          and abs(ok_cmp["smallest_nrt_rel_diff"] - 0.2) < 1e-9 and ok_cmp["arc_groups"] == 50 and not ok_cmp["arc_bitwise_identical"])
    check("A1: arc 2e-3 apart fails", not arc_nrt_comparison([v * 1.002 for v in base], base, nrt_a, base)["pass"])
    check("A1: NRT 5e-3 apart in one group fails",
          not arc_nrt_comparison(near, base, [v * 1.2 for v in base[:10]] + [base[10] * 1.005] + [v * 1.2 for v in base[11:]], base)["pass"])
    check("A1: floor ignores tiny groups, empty fails",
          arc_nrt_comparison(near[:-1] + [5e-9], base, nrt_a[:-1] + [1e-9], base)["pass"]
          and not arc_nrt_comparison([], [], [], [])["pass"])
    check("energy-source key is exact", ENERGY_SOURCE_KEY == "displacement_energy_source"
          and strip_energy_source({"displacement_energy_eV": {"Fe": 40.0}, "displacement_energy_source": {"Fe": "table"}})[0]
          == {"displacement_energy_eV": {"Fe": 40.0}})
    abs_spec = absolute_spec(json.loads(p154.GATE_SPEC.read_text()), "/lib")
    check("spec paths", abs_spec["library"]["path"].startswith("/lib/activation/") and abs_spec["decay"]["primary"].startswith("/lib/decay/"))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        (src / "n-Fe056.tendl").write_text("x")
        (Path(tmp) / "link").symlink_to(src / "n-Fe056.tendl")
        staged = stage_corpus(Path(tmp) / "c", ["n-Fe056.tendl"], src)
        check("corpus copies are regular files", (staged / "n-Fe056.tendl").is_file() and not (staged / "n-Fe056.tendl").is_symlink())
        deck_src = Path(tmp) / "fe"
        deck_src.mkdir()
        (deck_src / "d_5min.i").write_text("CLOBBER\nGETXS 0\nGETDECAY 0\nFISPACT\n")
        (deck_src / "d_5min_fluxes").write_text("1 2 3\n")
        deck = fns_deck_with(Path(tmp) / "out", deck_src, "ALLDISPEN 40.0")
        check("deck insertion after GETDECAY", deck.read_text().splitlines()[3] == "ALLDISPEN 40.0"
              and (deck.parent / "d_5min_fluxes").is_file())
    print(f"self-check: {'FAILED ' + str(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


# ------------------------------------------------------------------------------ main


def guarded(fn):
    try:
        return fn()
    except (GateError, KeyError, ValueError, OSError, TypeError, StopIteration) as error:
        return {"pass": False, "error": f"{type(error).__name__}: {error}"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--gates", default=",".join(GATES), help="comma-separated gates to run (default all)")
    ap.add_argument("--workdir", default="target/p156-work")
    ap.add_argument("--out", default="results/p156_damage.json")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--data-dir", default="actinv-data", help="root holding the fetched v1.2.0 data (G5 decay, G6, G7)")
    ap.add_argument("--library-dir", default=str(LIBRARY_DIR), help="folder holding activation/ and decay/ for the example specs")
    ap.add_argument("--catalog", default=str(CATALOG_NEW))
    ap.add_argument("--tendl-dir", default=str(TENDL_DIR))
    ap.add_argument("--fns", default=str(FNS_DIR), help="FNS deck root holding Fe/")
    ap.add_argument("--table", help="the shipped actinv-damage-table-2 file (G5, G6)")
    ap.add_argument("--build-log", help="GNU `time -v` log of the shipped table's build (G5)")
    ap.add_argument("--decay-primary")
    ap.add_argument("--decay-fallback")
    ap.add_argument("--g1-log")
    ap.add_argument("--staged", help="staged release assets directory with SHA256SUMS and SIZES (G7)")
    ap.add_argument("--fetch-check", action="store_true", help="G7: fetch into a directory seeded with the v1.1.0 files")
    ap.add_argument("--fetch-env", action="append", help="KEY=VALUE for the fetch (for example a mirror setting)")
    ap.add_argument("--network-log", help="G7: file of requested URLs, one per line, from the fetch")
    ap.add_argument("--published", action="store_true", help="G7: clean fetch, verify and FNS run from the published release")
    ap.add_argument("--energy-source-key", default=ENERGY_SOURCE_KEY, help="exact name of the ledger.damage energy-source key (G6)")
    ap.add_argument("--njoy", default=njoy.DEFAULT_NJOY)
    ap.add_argument("--njoy-err", type=float, default=njoy.RECONR_ERR)
    ap.add_argument("--mode-information", action="store_true", help="G4: also build P154's other capture / continuum modes")
    ap.add_argument("--reuse", action="store_true", help="reuse built tables and NJOY tapes found in the workdir")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    wanted = [g.strip().upper() for g in args.gates.split(",") if g.strip()]
    unknown = [g for g in wanted if g not in GATES]
    if unknown:
        ap.error(f"unknown gates {unknown}")
    needs_binary = [g for g in wanted if g in ("G2", "G3", "G4", "G5", "G6", "G7")]
    if needs_binary and not args.candidate:
        ap.error("--candidate is required for " + ",".join(needs_binary))
    if any(g in wanted for g in ("G2", "G3")) and not args.reference:
        ap.error("--reference is required for G2 and G3")
    ref = str(Path(args.reference).resolve()) if args.reference else None
    cand = str(Path(args.candidate).resolve()) if args.candidate else None
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    bounds = p154.vendored_boundaries()

    table_path = args.table
    if not table_path and Path(args.catalog).is_file():
        found, _art = table_path_in(args.data_dir, json.loads(Path(args.catalog).read_text()))
        table_path = str(found) if found else None
    args.table = args.table or table_path

    gates = {}
    runners = {
        "G0": gate_g0,
        "G1": lambda: gate_g1(args.g1_log),
        "G2": lambda: gate_g2(ref, cand, work, args.timeout, args.reuse),
        "G3": lambda: gate_g3(ref, cand, work, args.tendl_dir, args.timeout),
        "G4": lambda: gate_g4(cand, work, args, bounds),
        "G5": lambda: gate_g5(cand, work, args),
        "G6": lambda: gate_g6(cand, work, args, table_path),
        "G7": lambda: gate_g7(cand, work, args),
        "G8": lambda: {"pass": None, "external": True, "note": "CI green on the pushed commit; committed baselines (external)"},
    }
    for g in GATES:
        if g in wanted:
            gates[g] = guarded(runners[g])
    judged = [g for g in wanted if g not in EXTERNAL]
    passed = all(gates[g].get("pass") is True for g in judged)
    full = all(g in wanted for g in GATES if g not in EXTERNAL)
    verdict = "FAIL" if not passed else ("PASS" if full else "PARTIAL")
    record = {"protocol": "protocols/ACTINV-P156_PROTOCOL.md", "protocol_sha256": sha256(PROTOCOL),
              "reference_sha256": sha256(ref) if ref else None, "candidate_sha256": sha256(cand) if cand else None,
              "checker_sha256": sha256(__file__), "gates_run": wanted, "shipped_table": str(table_path) if table_path else None,
              "tolerances": {"G5_row_sum": TOL_SUM, "G6_run": TOL_RUN, "P154_G3_elastic": p154.TOL_G3,
                             "P154_G4": p154.TOL_G4, "P154_G4_uncovered_fraction": p154.G4_UNCOVERED_FRACTION},
              "standard_displacement_energy_eV": STANDARD_ED, "default_displacement_energy_eV": DEFAULT_ED,
              "gates": gates, "verdict": verdict}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
    for g in wanted:
        state = gates[g].get("pass")
        print(f"P156 {g}: {'external' if g in EXTERNAL else ('PASS' if state else 'FAIL')}")
        if "error" in gates[g]:
            print(f"  {g}: ERROR {gates[g]['error']}")
        for label, case in (gates[g].get("cases") or gates[g].get("checks") or gates[g].get("per_target") or {}).items():
            if isinstance(case, dict) and "pass" in case:
                print(f"  {g}/{label}: {'PASS' if case.get('pass') else 'FAIL'}"
                      + (f" ERROR {case['error']}" if "error" in case else ""))
    print(f"P156: {verdict} ({out})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
