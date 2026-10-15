#!/usr/bin/env python3
"""P138 checker: `actinv import-fispact` maps the parity keywords (protocols/ACTINV-P138_PROTOCOL.md).

G2  No regression. `controls/check_p132.py --candidate CAND` (the 132 FNS decks import, validate, keep their schedules
    and reproduce the hand-written FNS Fe run) is run in a fresh working directory and must exit 0 with verdict
    P132-PASS.
G3  Equivalence. Each keyword with a runnable meaning is inserted into a small deck derived from the FNS Fe 5-minute
    deck (UNCERT, MIND and NOSTABLE removed, three cooling intervals kept), imported with the candidate (catalog
    library and decay references, as controls/check_p132.py does), run, and compared with a spec this script writes
    from the keyword's meaning, starting from examples/fns_fe_5min.json:
      WALL         step `wall_loading_MW_m2`, spectrum `wall_loading_MW_m2` = the fluxes file's loading, `total` = the
                   file's sum (FISPACT-II 4.91: flux scales by wall / wall-in-file); and, as a second hand spec, the
                   same irradiation as an explicit `flux` multiplier wall / wall-in-file (the results may differ
                   only under ledger.wall_loading and certificate.wall_loading)
      PARTITION    `partition` {Mn: 0.1, Cr: 0.5} on the first cooling step
      OVER ALAM    `overrides` half_life Mn54 = 100 d
      OVER ACROSS  `overrides` cross_section Fe54 MT=103 (the (n,p) channel to Mn54) = 0.5 b
      GRPCONVERT   175-group file (this script aggregates the 709 group spectrum onto the VITAMIN-J 175 structure from
                   the vendored boundary files) with `spectrum.rebin`
      DOSE 2       `photon.point_source.distance_m`
      GROUP 1      `photon.group_structure` steiner-22
      BREMSSTRAHLUNG  `photon.bremsstrahlung.nuclides`
      PATH         `pathways.targets`
      ATWO         `radiological.table` bundled:us-49cfr173-a2
      POWER        step `power_W_cm3`, `material.density_g_cm3` from DENSITY and a `kerma` section whose table this
                   script writes (deterministic synthetic rows, as controls/check_p126.py does; the NJOY table of the
                   P126 run holds Fe-56 only) and adds to the imported spec, which the importer documents as required.
    The results must be equal apart from echoes (`ms`, `spec_title`, certificate paths and the declared library
    hash). Floats are compared to 1e-12 relative (the exact-equal flag is recorded); everything else exactly. Every
    hand spec must also give a result different from the unkeyworded base run, so a keyword that both sides ignore
    cannot pass. Report-only extras: CNVTYPE 1 (equal_energy) and LIMGRP.
G4  Honesty. For every variant the report statuses of its keywords are collected. A keyword that reports `mapped`
    while its run differs from its hand spec fails (this applies to the extras too); any status other than `mapped`
    must carry a reason. Negative controls (import only): ADLAM, ADCROSS, an ambiguous or isomer ACROSS daughter,
    a trailing PARTITION, BREMSSTRAHLUNG without nuclides, DOSE 3, GROUP 2, PATH beside POWER, a spectrum file whose
    group count is not a standard structure, and a 175-group file without GRPCONVERT must each be reported as
    `unsupported` (or stop the import) and leave the corresponding spec field absent.

G1 (fmt, clippy, tests) and G5 (CI) are not checked here. Every binary call is serial, without a shell and under a
timeout, from the repository root.

Usage:
  check_p138.py --candidate BIN [--workdir target/p138-work] [--out results/p138_importer_keywords.json]
      [--fns ~/nuclear-data/conderc-fns/fns] [--timeout 3600] [--p132-timeout 14400] [--skip-p132]

Exit 0 on PASS, 1 on FAIL, 2 on a missing input.
"""
import argparse
import array
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P138_PROTOCOL.md"
CHECK_P132 = ROOT / "controls" / "check_p132.py"
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEFAULT_FNS = Path("~/nuclear-data/conderc-fns/fns").expanduser()
DECK_REL = "Fe/TENDL-2017_1996exp_5min.i"
FLUXES_REL = "Fe/1996exp_5min_fluxes"
GROUPS_709 = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
GROUPS_175 = ROOT / "crates" / "actinv-data" / "data" / "fispact_175_groups.json"
LIBRARY = ROOT / "actinv-data" / "v1.1.0" / "activation" / "tendl-2025-neutron-709g.npz"
PHOTON_RESPONSE = Path("~/nuclear-data/photon-response/nist-xcom-air-fe.json").expanduser()
STATUSES = ("mapped", "approximated", "noted", "unsupported")

COOLING_KEPT = 3
WALL_MW_M2 = 2.5
POWER_W_CM3 = 5.0e-4
PARTITION_KEPT = {"Mn": 0.1, "Cr": 0.5}
ALAM_DAYS = 100.0
ACROSS_BARNS = 0.5
POINT_SOURCE_M = 1.0
BREMS_NUCLIDE = "Mn56"
PATH_TARGET = "Mn54"
LIMGRP_EV = (1.0, 1.0e7)
REL_TOL = 1e-12
MAX_LISTED = 20
MAX_DIFFS = 400

# Echoes that may differ between two runs of the same problem written differently.
ECHOES = ["/ms", "/spec_title", "/certificate/library", "/certificate/library_sha256_declared",
          "/certificate/decay_primary", "/certificate/decay_fallback", "/certificate/inputs/library/path",
          "/certificate/inputs/library_index/path", "/certificate/inputs/decay_primary/path",
          "/certificate/inputs/decay_fallback/path"]
# A wall step and its explicit-multiplier twin differ in exactly these places (as in controls/check_p124.py G4).
WALL_TWIN_ECHOES = ["/ledger/wall_loading", "/certificate/wall_loading"]

AMBIGUITIES = [
    "PARTITION: FISPACT-II applies the split when it reads the keyword, ACTINV at the start of the next step; the "
    "decks here put the keyword directly before the cooling TIME it precedes, where the two agree.",
    "OVER ACROSS: FISPACT-II sets one channel (target to the named daughter); ACTINV sets the whole ENDF reaction "
    "(MT=103 here) and keeps its product branching. The hand spec states the ACTINV meaning, and the importer must "
    "say `approximated`.",
    "LIMGRP: the importer reads the two numbers as energies in eV; whether FISPACT-II means energies or group "
    "numbers is not settled by the manual text the importer cites, so the extra variant is report-only.",
    "POWER: p is W/cc normalized on the kerma of the listed MTs; ACTINV normalizes on the table's total (MT=301). "
    "The synthetic table is not evaluated data, so the check is equivalence of the two specs, not heating accuracy.",
    "DOSE 2: with the installed response (no air attenuation curve) the point-source dose is null with a stated "
    "reason, so the check covers the configuration and the withheld-dose path, not a dose value.",
    "ATWO: the table is the US 49 CFR 173 A2 table, not FISPACT-II's own A2 file (status `approximated`).",
    "BREMSSTRAHLUNG: reported in a separate block and not added to the decay-photon dose (status `approximated`).",
    "PATH/ROUTES: numeric arguments are not interpreted; ACTINV's route search thresholds keep their defaults.",
]


# ------------------------------------------------------------------------------ helpers


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def run_cmd(argv, timeout, env=None):
    """Run without a shell from the repository root. Returns (exit code or None, stdout, stderr)."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, b"", f"timed out after {timeout} s".encode()
    except OSError as error:
        return None, b"", f"{type(error).__name__}: {error}".encode()
    return proc.returncode, proc.stdout, proc.stderr


def tail(data, n=500):
    return data.decode(errors="replace")[-n:]


TIMING_KEYS = ("wall_time_s", "wall_s", "cells_per_s")


def is_allowed(path, allowed):
    """Whether a differing path is a listed echo or a wall-clock timing key."""
    last = path.rsplit("/", 1)[-1]
    if last in TIMING_KEYS or last.endswith("_ms"):
        return True
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "[") for p in allowed)


def result_diffs(a, b, path="", out=None, tol=REL_TOL):
    """Differing JSON paths. Floats within `tol` relative are tolerated (their worst size is returned); other leaves
    must be equal. Returns (diffs, worst tolerated relative difference, exact)."""
    out = [] if out is None else out
    state = {"worst": 0.0, "exact": True}

    def short(value):
        text = repr(value)
        return text if len(text) <= 80 else text[:77] + "..."

    def walk(x, y, where):
        if len(out) >= MAX_DIFFS:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for key in sorted(set(x) | set(y)):
                if key not in x or key not in y:
                    out.append({"path": f"{where}/{key}", "a": "<absent>" if key not in x else short(x[key]),
                                "b": "<absent>" if key not in y else short(y[key])})
                else:
                    walk(x[key], y[key], f"{where}/{key}")
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                out.append({"path": where, "a": f"length {len(x)}", "b": f"length {len(y)}"})
                return
            for i, (p, q) in enumerate(zip(x, y)):
                walk(p, q, f"{where}[{i}]")
        elif (isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool)
              and not isinstance(y, bool)):
            if x == y:
                return
            scale = max(abs(x), abs(y))
            rel = abs(x - y) / scale if scale else 0.0
            if rel <= tol:
                state["worst"] = max(state["worst"], rel)
                state["exact"] = False
            else:
                out.append({"path": where, "a": short(x), "b": short(y)})
        elif x != y or type(x) is not type(y):
            out.append({"path": where, "a": short(x), "b": short(y)})

    walk(a, b, path)
    return out, state["worst"], state["exact"]


def compare_runs(a, b, allowed):
    diffs, worst, exact = result_diffs(a, b)
    other = [d for d in diffs if not is_allowed(d["path"], allowed)]
    return {"n_differences": len(diffs), "n_echo": len(diffs) - len(other), "n_other": len(other),
            "other": other[:MAX_LISTED], "identical_apart_from_echoes": not other,
            "exact": exact and not other, "worst_tolerated_relative": worst}


# ------------------------------------------------------------------------------ deck and spectrum files


def number(word):
    return float(word.replace("D", "E").replace("d", "e"))


def parse_fluxes(path):
    """Group values (highest energy first) of a FISPACT-II fluxes file and its trailing first-wall loading."""
    lines = Path(path).read_text().splitlines()
    last = max(i for i, line in enumerate(lines) if line.strip())
    values = [number(word) for line in lines[:last] for word in line.split()]
    return values[:-1], values[-1]


def write_fluxes(path, values, wall, title):
    lines = []
    for i in range(0, len(values), 6):
        lines.append(" " + " ".join(repr(float(v)) for v in values[i:i + 6]))
    lines.append(f" {wall!r}")
    lines.append(title)
    Path(path).write_text("\n".join(lines) + "\n")


def load_boundaries(path):
    data = json.loads(Path(path).read_text())
    ascending = sorted(float(v) for v in data["boundaries_eV"])
    if any(y <= x for x, y in zip(ascending, ascending[1:])):
        raise ValueError(f"{path}: boundaries are not strictly increasing")
    return ascending


def rebin(sb, sf, db, convention="equal_lethargy"):
    """Spread each source group's flux over the destination groups it overlaps, uniformly in lethargy (or energy);
    flux outside the destination range is dropped. Plain overlap loop."""
    out = [[] for _ in range(len(db) - 1)]
    for g, flux in enumerate(sf):
        if flux == 0.0:
            continue
        lo, hi = sb[g], sb[g + 1]
        for d in range(len(db) - 1):
            a, b = max(lo, db[d]), min(hi, db[d + 1])
            if b > a:
                share = math.log(b / a) / math.log(hi / lo) if convention == "equal_lethargy" else (b - a) / (hi - lo)
                out[d].append(flux * share)
    return [math.fsum(p) for p in out]


def aggregate_descending(values709, bounds_709_asc, bounds_asc):
    """709-group file values (highest first) onto another ascending structure; result highest first."""
    flux_asc = list(reversed(values709))
    return list(reversed(rebin(bounds_709_asc, flux_asc, bounds_asc)))


def npz_floats(path, member):
    """A one-dimensional little-endian float64 array of an NPZ, read without numpy."""
    assert sys.byteorder == "little"
    with zipfile.ZipFile(path) as archive:
        data = archive.read(member)
    assert data[:6] == b"\x93NUMPY"
    major = data[6]
    if major == 1:
        start = 10 + int.from_bytes(data[8:10], "little")
    else:
        start = 12 + int.from_bytes(data[8:12], "little")
    values = array.array("d")
    values.frombytes(data[start:])
    return list(values)


def synthetic_kerma_table(bounds):
    """Deterministic positive kerma rows (barn eV per group): the iron isotopes by name, Fe/Mn/Cr/Co by element."""
    def row(base, phase):
        return [base * (1.0 + 0.5 * math.sin(0.11 * g + phase)) for g in range(len(bounds) - 1)]
    return {
        "format": "actinv-kerma-table-1",
        "source": {"citation": "synthetic deterministic table for the P138 checker (not evaluated data)",
                   "edition": "controls/check_p138.py synthetic_kerma_table", "url": "local"},
        "projectile": "neutron", "group_structure": "fispact-709", "boundaries_eV": bounds,
        "units": "kerma_barn_eV_per_group",
        "targets": {"Fe54": row(2.0e6, 0.1), "Fe56": row(3.0e6, 0.7), "Fe57": row(2.5e6, 1.3),
                    "Fe58": row(2.2e6, 1.9), "Fe": row(2.7e6, 2.5), "Mn": row(2.9e6, 3.1), "Cr": row(2.4e6, 3.7),
                    "Co": row(2.6e6, 4.3)},
        "temperature_K": 293.6,
    }


def first_word(line):
    words = line.split()
    return words[0].upper() if words else ""


def derive_deck(deck_text, title, before_flux=(), flux_line=None, before_cooling=(), after_cooling=(),
                keep_cooling=COOLING_KEPT):
    """The FNS Fe deck with UNCERT, MIND and NOSTABLE dropped, the title replaced, `before_flux` inserted ahead of the
    FLUX line (which `flux_line` replaces), `before_cooling` inserted right after ZERO, and `keep_cooling` of the TIME
    lines that follow. `after_cooling` goes after the last of them, before END."""
    out, cooling, seen_zero = [], 0, False
    for line in deck_text.splitlines():
        word = first_word(line)
        if word.startswith("UNCER") or word == "MIND" or word.startswith("NOSTA"):
            continue
        if line.startswith("* FNS 5 Min Fe"):
            out.append(f"* {title}")
            continue
        if word == "FLUX" and not seen_zero and number(line.split()[1]) > 0.0:
            out.extend(before_flux)
            out.append(flux_line if flux_line is not None else line)
            continue
        if word == "ZERO":
            seen_zero = True
            out.append(line)
            out.extend(before_cooling)
            continue
        if seen_zero:
            # after ZERO only the cooling TIME lines matter; END, the trailing text line and the `/*` marker go
            if word == "TIME":
                cooling += 1
                if cooling <= keep_cooling:
                    out.append(line)
            continue
        out.append(line)
    out.extend(after_cooling)
    out.append("END")
    return "\n".join(out) + "\n"


def deck_numbers(deck_text):
    """The first positive FLUX, the DENSITY and the cooling TIME lengths of the FNS deck, parsed here."""
    flux = density = None
    cooling, seen_zero = [], False
    for line in deck_text.splitlines():
        word, words = first_word(line), line.split()
        if word == "FLUX" and flux is None and number(words[1]) > 0.0:
            flux = number(words[1])
        elif word == "DENSITY":
            density = number(words[1])
        elif word == "ZERO":
            seen_zero = True
        elif seen_zero and word == "TIME":
            cooling.append(number(words[1]))
    return flux, density, cooling[:COOLING_KEPT]


# ------------------------------------------------------------------------------ hand-written specs


class Hand:
    """Builds the hand-written specs from examples/fns_fe_5min.json and the numbers parsed from the deck."""

    def __init__(self, example, values709, wall709, flux_total, density, cooling):
        self.example, self.values709, self.wall709 = example, values709, wall709
        self.flux_total, self.density, self.cooling = flux_total, density, cooling

    def base(self, title, values=None):
        spec = copy.deepcopy(self.example)
        spec["title"] = title
        spec["spectrum"] = {"structure": "fispact-709", "flux_per_group": list(values or self.values709),
                            "total": self.flux_total, "descending": True}
        spec["schedule"] = [{"dt": "300 s", "flux": 1.0}] + [{"dt": f"{t:g} s", "flux": 0.0} for t in self.cooling]
        return spec

    def wall_field(self, title):
        spec = self.base(title)
        spec["spectrum"]["total"] = sum(self.values709)
        spec["spectrum"]["wall_loading_MW_m2"] = self.wall709
        spec["schedule"][0] = {"dt": "300 s", "wall_loading_MW_m2": WALL_MW_M2}
        return spec

    def wall_multiplier(self, title):
        spec = self.base(title)
        spec["spectrum"]["total"] = sum(self.values709)
        spec["schedule"][0] = {"dt": "300 s", "flux": WALL_MW_M2 / self.wall709}
        return spec

    def partition(self, title):
        spec = self.base(title)
        spec["schedule"][1]["partition"] = dict(PARTITION_KEPT)
        return spec

    def over_alam(self, title):
        spec = self.base(title)
        spec["overrides"] = [{"nuclide": "Mn54", "kind": "half_life", "value": ALAM_DAYS, "units": "d"}]
        return spec

    def over_across(self, title):
        spec = self.base(title)
        spec["overrides"] = [{"nuclide": "Fe54", "kind": "cross_section", "mt": 103, "value": ACROSS_BARNS}]
        return spec

    def rebin(self, title, values175, bounds175, convention, limits=None):
        spec = self.base(title, values175)
        block = {"convention": convention}
        if limits:
            block["emin_eV"], block["emax_eV"] = limits
        spec["spectrum"]["boundaries_eV"] = list(bounds175)
        spec["spectrum"]["rebin"] = block
        return spec

    def photon(self, title, block, response):
        spec = self.base(title)
        spec["photon"] = dict(block)
        if response:
            spec["photon"]["response"] = response
        return spec

    def pathways(self, title):
        spec = self.base(title)
        spec["pathways"] = {"targets": [PATH_TARGET]}
        return spec

    def atwo(self, title):
        spec = self.base(title)
        spec["radiological"] = {"table": {"path": "bundled:us-49cfr173-a2"}}
        return spec

    def power(self, title, kerma):
        spec = self.base(title)
        spec["material"]["density_g_cm3"] = self.density
        spec["spectrum"].pop("total")
        spec["schedule"][0] = {"dt": "300 s", "power_W_cm3": POWER_W_CM3}
        spec["kerma"] = {"table": dict(kerma)}
        return spec


# ------------------------------------------------------------------------------ the work


class Work:
    def __init__(self, candidate, directory, timeout):
        self.candidate, self.dir, self.timeout = candidate, directory, timeout
        self.runs = {}

    def run(self, label, spec):
        """`candidate run SPEC OUT`. Returns the parsed result, or None (the failure is recorded in `runs`)."""
        spec_path, out_path = self.dir / f"{label}.spec.json", self.dir / f"{label}.result.json"
        spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        info = {"spec": record_file(spec_path)}
        self.runs[label] = info
        started = time.monotonic()
        code, _, err = run_cmd([self.candidate, "run", spec_path, out_path], self.timeout)
        info.update({"exit_code": code, "wall_s": round(time.monotonic() - started, 3)})
        if code != 0:
            info["stderr_tail"] = tail(err)
            return None
        info["output"] = record_file(out_path)
        return json.loads(out_path.read_text())

    def import_deck(self, label, deck_text, fluxes, post=None):
        """`candidate import-fispact DECK OUT --fluxes F`. Returns a record with the spec, the report and the exit code;
        `post` (a function of the spec) edits the imported spec before it is written back for the run."""
        deck_path, out_path = self.dir / f"{label}.i", self.dir / f"{label}.imported.json"
        deck_path.write_text(deck_text)
        report_path = self.dir / f"{label}.imported.import.json"
        code, out, err = run_cmd([self.candidate, "import-fispact", deck_path, out_path, "--fluxes", fluxes],
                                 self.timeout)
        rec = {"deck": record_file(deck_path), "fluxes": record_file(fluxes), "exit_code": code,
               "stdout_tail": tail(out, 300), "stderr_tail": tail(err, 300), "spec": None, "report": None}
        if report_path.is_file():
            rec["report"] = json.loads(report_path.read_text())
        if code == 0 and out_path.is_file():
            rec["spec"] = json.loads(out_path.read_text())
            if post is not None:
                rec["spec"] = post(rec["spec"])
                out_path.write_text(json.dumps(rec["spec"], indent=1) + "\n")
            rec["spec_file"] = record_file(out_path)
        return rec


def keyword_entries(report, keywords):
    rows = []
    for entry in (report or {}).get("entries", []):
        if entry["keyword"] in keywords:
            rows.append({"keyword": entry["keyword"], "line": entry["line"], "status": entry["status"],
                         "detail": entry["detail"]})
    return rows


def run_variant(work, variant, baseline, fluxes_for):
    """Import, run and compare one G3 variant. Returns its record."""
    name = variant["name"]
    rec = {"name": name, "gating": variant.get("gating", True), "keywords": variant["keywords"]}
    imported = work.import_deck(f"v_{name}", variant["deck"], fluxes_for[variant["fluxes"]], variant.get("post"))
    rec["import"] = {k: v for k, v in imported.items() if k not in ("spec", "report")}
    rec["statuses"] = keyword_entries(imported["report"], variant["keywords"])
    rec["report_counts"] = (imported["report"] or {}).get("counts")
    if imported["spec"] is None:
        rec.update({"pass": False, "error": "the import produced no spec", "comparisons": {}})
        return rec
    result = work.run(f"v_{name}_imported", imported["spec"])
    comparisons, sensitive, ok = {}, {}, result is not None
    for label, (spec, extra_echoes) in variant["hands"].items():
        hand_result = work.run(f"v_{name}_hand_{label}", spec)
        if hand_result is None or result is None:
            comparisons[label] = {"error": "a run failed; see runs", "identical_apart_from_echoes": False}
            ok = False
            continue
        comparisons[label] = compare_runs(result, hand_result, ECHOES + extra_echoes)
        # the keyword must matter: the hand spec's result differs from the unkeyworded base run
        base_diff = compare_runs(hand_result, baseline, ECHOES + extra_echoes)
        sensitive[label] = {"n_differences_from_base": base_diff["n_other"], "keyword_changes_result":
                            base_diff["n_other"] > 0}
        ok = ok and comparisons[label]["identical_apart_from_echoes"] and base_diff["n_other"] > 0
    rec["comparisons"], rec["sensitivity"] = comparisons, sensitive
    rec["imported_run_ok"] = result is not None
    rec["pass"] = bool(ok)
    return rec


def negative_controls(work, deck_text, fluxes_709, fluxes_175, fluxes_50):
    """Import-only controls: each must be reported as not faithfully mapped, with the spec field absent."""
    def deck(title, **kw):
        return derive_deck(deck_text, title, **kw)

    cases = [
        {"name": "adlam", "deck": deck("P138 neg ADLAM", before_flux=["OVER MN54", " ADLAM 0.1"]),
         "expect": {"ADLAM": {"unsupported"}}, "absent": ["overrides"]},
        {"name": "adcross", "deck": deck("P138 neg ADCROSS", before_flux=["OVER FE54", " ADCROSS MN54 0.1"]),
         "expect": {"ADCROSS": {"unsupported"}}, "absent": ["overrides"]},
        {"name": "across_ambiguous", "deck": deck("P138 neg ACROSS channel", before_flux=["OVER FE56", " ACROSS MN54 1.0"]),
         "expect": {"ACROSS": {"unsupported"}}, "absent": ["overrides"]},
        {"name": "across_isomer", "deck": deck("P138 neg ACROSS isomer", before_flux=["OVER FE56", " ACROSS MN56M 1.0"]),
         "expect": {"ACROSS": {"unsupported"}}, "absent": ["overrides"]},
        {"name": "partition_trailing", "deck": deck("P138 neg PARTITION", after_cooling=["PARTITION MN 0.1"]),
         "expect": {"PARTITION": {"unsupported"}}, "absent": [("schedule", 1, "partition")]},
        {"name": "brems_no_nuclides", "deck": deck("P138 neg BREMSSTRAHLUNG", before_flux=["BREMSSTRAHLUNG"]),
         "expect": {"BREMSSTRAHLUNG": {"unsupported"}}, "absent": [("photon", "bremsstrahlung")]},
        {"name": "dose3", "deck": deck("P138 neg DOSE 3", before_flux=["DOSE 3 1.0"]),
         "expect": {"DOSE": {"unsupported"}}, "absent": [("photon", "point_source")]},
        {"name": "group2", "deck": deck("P138 neg GROUP 2", before_flux=["GROUP 2"]),
         "expect": {"GROUP": {"unsupported"}}, "absent": [("photon", "group_structure")]},
        {"name": "path_beside_power",
         "deck": deck("P138 neg PATH beside POWER", before_flux=[f"PATH 1 {PATH_TARGET.upper()}"],
                      flux_line=f"POWER {POWER_W_CM3:g} 1 301"),
         "expect": {"PATH": {"unsupported"}}, "absent": ["pathways"]},
        {"name": "uncert_beside_across",
         "deck": deck("P138 neg UNCERT beside ACROSS", before_flux=["OVER FE54", " ACROSS MN54 0.5", "UNCERT 2"]),
         "expect": {"UNCERTAINTY": {"unsupported"}}, "absent": ["uncertainty"]},
        {"name": "grpconvert_50_groups",
         "deck": deck("P138 neg GRPCONVERT 50 groups", before_flux=["GRPCONVERT 50 709"]),
         "fluxes": fluxes_50, "expect_import_failure": True},
        {"name": "175_groups_without_grpconvert", "deck": deck("P138 neg 175 groups"), "fluxes": fluxes_175,
         "expect_import_failure": True},
    ]
    out = []
    for case in cases:
        rec = {"name": case["name"]}
        imported = work.import_deck(f"n_{case['name']}", case["deck"], case.get("fluxes", fluxes_709))
        rec["exit_code"] = imported["exit_code"]
        if case.get("expect_import_failure"):
            rec["stderr_tail"] = imported["stderr_tail"]
            rec["pass"] = imported["exit_code"] not in (0, None) and imported["spec"] is None
            out.append(rec)
            continue
        if imported["spec"] is None:
            rec.update({"pass": False, "error": "the import failed", "stderr_tail": imported["stderr_tail"]})
            out.append(rec)
            continue
        problems = []
        statuses = {}
        for keyword, allowed in case["expect"].items():
            rows = keyword_entries(imported["report"], {keyword})
            statuses[keyword] = [r["status"] for r in rows]
            if not rows or not any(r["status"] in allowed for r in rows):
                problems.append(f"{keyword}: statuses {statuses[keyword]}, expected one of {sorted(allowed)}")
            problems += [f"{keyword}: no reason given" for r in rows if r["status"] != "mapped" and not r["detail"].strip()]
        for path in case["absent"]:
            node, keys = imported["spec"], [path] if isinstance(path, str) else list(path)
            present = True
            for key in keys:
                try:
                    node = node[key]
                except (KeyError, IndexError, TypeError):
                    present = False
                    break
            if present:
                problems.append(f"spec field {'/'.join(map(str, keys))} is present")
        rec.update({"statuses": statuses, "problems": problems, "pass": not problems})
        out.append(rec)
    return out


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True, help="release binary of the integrated P138 implementation")
    ap.add_argument("--fns", default=str(DEFAULT_FNS), help="root of the FNS deck corpus")
    ap.add_argument("--workdir", default="target/p138-work")
    ap.add_argument("--out", default="results/p138_importer_keywords.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per import or run")
    ap.add_argument("--p132-timeout", type=int, default=14400, help="seconds allowed for the whole P132 check")
    ap.add_argument("--skip-p132", action="store_true", help="skip G2 (it then fails; for developing the variants)")
    args = ap.parse_args(argv)
    candidate = Path(args.candidate).resolve()
    fns = Path(os.path.expanduser(args.fns)).resolve()
    deck_path, fluxes_path = fns / DECK_REL, fns / FLUXES_REL
    missing = [f"{label}: {path}" for label, path in (
        ("candidate binary", candidate), ("FNS deck", deck_path), ("FNS fluxes file", fluxes_path),
        ("check_p132.py", CHECK_P132), ("gate spec", GATE_SPEC), ("protocol", PROTOCOL), ("library", LIBRARY),
        ("709-group boundaries", GROUPS_709), ("175-group boundaries", GROUPS_175)) if not Path(path).exists()]
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    work_root = Path(args.workdir)
    work_root = work_root if work_root.is_absolute() else ROOT / work_root
    tag = f"{int(time.time())}-{os.getpid()}"
    run_dir = work_root / f"run-{tag}"
    run_dir.mkdir(parents=True, exist_ok=False)
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path

    gates = {"G1": {"external": "fmt, clippy -D warnings and workspace tests are not run here"},
             "G5": {"external": "CI on the pushed commit is not run here"}}

    # ---- G2: the P132 checker, in its own fresh working directory
    p132_dir = run_dir / "p132"
    p132_out = p132_dir / "p132_import.json"
    g2 = {"argv": [sys.executable, str(CHECK_P132), "--candidate", str(candidate), "--workdir", str(p132_dir),
                   "--out", str(p132_out), "--timeout", str(args.timeout)]}
    if args.skip_p132:
        g2.update({"pass": False, "skipped": True})
    else:
        started = time.monotonic()
        code, stdout, stderr = run_cmd(g2["argv"], args.p132_timeout)
        g2.update({"exit_code": code, "wall_s": round(time.monotonic() - started, 1),
                   "stdout_tail": tail(stdout, 800), "stderr_tail": tail(stderr, 400)})
        if p132_out.is_file():
            record = json.loads(p132_out.read_text())
            g2["p132_verdict"] = record.get("verdict")
            g2["p132_gates"] = {g: bool(record["gates"].get(g, {}).get("pass")) for g in ("G2", "G3", "G4")}
            g2["p132_record"] = record_file(p132_out)
        g2["pass"] = bool(code == 0 and g2.get("p132_verdict") == "P132-PASS")
    gates["G2"] = g2

    # ---- inputs for G3 / G4
    deck_text = deck_path.read_text()
    values709, wall709 = parse_fluxes(fluxes_path)
    flux_total, density, cooling = deck_numbers(deck_text)
    example = json.loads(GATE_SPEC.read_text())
    hand = Hand(example, values709, wall709, flux_total, density, cooling)
    b709, b175 = load_boundaries(GROUPS_709), load_boundaries(GROUPS_175)
    files_dir = run_dir / "inputs"
    files_dir.mkdir()
    values175 = aggregate_descending(values709, b709, b175)
    fluxes_175 = files_dir / "fluxes_175"
    write_fluxes(fluxes_175, values175, wall709, "P138 175-group aggregate of the FNS Fe 5 min spectrum")
    values175_read, _ = parse_fluxes(fluxes_175)
    b50 = [b709[0] * (b709[-1] / b709[0]) ** (i / 50.0) for i in range(51)]
    fluxes_50 = files_dir / "fluxes_50"
    write_fluxes(fluxes_50, list(reversed(rebin(b709, list(reversed(values709)), b50))), wall709,
                 "P138 50-group aggregate (not a FISPACT-II standard structure)")
    fluxes_for = {"709": fluxes_path, "175": fluxes_175}
    kerma_path = files_dir / "synthetic_fe_kerma.json"
    kerma_path.write_text(json.dumps(synthetic_kerma_table(npz_floats(LIBRARY, "bounds.npy")), indent=1) + "\n")
    kerma_ref = {"path": str(kerma_path), "sha256": sha256_file(kerma_path)}
    response = None
    if PHOTON_RESPONSE.is_file():
        response = {"path": str(PHOTON_RESPONSE), "sha256": sha256_file(PHOTON_RESPONSE)}

    def add_kerma(spec):
        spec["kerma"] = {"table": dict(kerma_ref)}
        return spec

    def add_response(spec):
        if response:
            spec["photon"]["response"] = dict(response)
        return spec

    def keyed(name, title, **kw):
        return derive_deck(deck_text, title, **kw)

    no_echo = []
    variants = [
        {"name": "wall", "keywords": ["WALL"], "fluxes": "709",
         "deck": keyed("wall", "P138 WALL", flux_line=f"WALL {WALL_MW_M2:g}"),
         "hands": {"wall_loading_field": (hand.wall_field("P138 hand WALL field"), no_echo),
                   "explicit_multiplier": (hand.wall_multiplier("P138 hand WALL multiplier"), WALL_TWIN_ECHOES)}},
        {"name": "partition", "keywords": ["PARTITION"], "fluxes": "709",
         "deck": keyed("partition", "P138 PARTITION",
                       before_cooling=[f"PARTITION {len(PARTITION_KEPT)} MN {PARTITION_KEPT['Mn']:g} "
                                       f"CR {PARTITION_KEPT['Cr']:g}"]),
         "hands": {"partition_field": (hand.partition("P138 hand PARTITION"), no_echo)}},
        {"name": "over_alam", "keywords": ["OVER", "ALAM"], "fluxes": "709",
         "deck": keyed("over_alam", "P138 OVER ALAM", before_flux=["OVER MN54", f" ALAM {ALAM_DAYS:g} DAYS"]),
         "hands": {"half_life_override": (hand.over_alam("P138 hand OVER ALAM"), no_echo)}},
        {"name": "over_across", "keywords": ["OVER", "ACROSS"], "fluxes": "709",
         "deck": keyed("over_across", "P138 OVER ACROSS", before_flux=["OVER FE54", f" ACROSS MN54 {ACROSS_BARNS:g}"]),
         "hands": {"cross_section_override": (hand.over_across("P138 hand OVER ACROSS"), no_echo)}},
        {"name": "grpconvert", "keywords": ["GRPCONVERT"], "fluxes": "175",
         "deck": keyed("grpconvert", "P138 GRPCONVERT", before_flux=["GRPCONVERT 175 709"]),
         "hands": {"rebin_equal_lethargy": (hand.rebin("P138 hand GRPCONVERT", values175_read, b175,
                                                       "equal_lethargy"), no_echo)}},
        {"name": "dose2", "keywords": ["DOSE"], "fluxes": "709", "post": add_response,
         "deck": keyed("dose2", "P138 DOSE 2", before_flux=[f"DOSE 2 {POINT_SOURCE_M:g}"]),
         "hands": {"point_source": (hand.photon("P138 hand DOSE 2", {"point_source": {"distance_m": POINT_SOURCE_M}},
                                                response), no_echo)}},
        {"name": "group1", "keywords": ["GROUP"], "fluxes": "709", "post": add_response,
         "deck": keyed("group1", "P138 GROUP 1", before_flux=["GROUP 1"]),
         "hands": {"steiner_22": (hand.photon("P138 hand GROUP 1", {"group_structure": "steiner-22"}, response),
                                  no_echo)}},
        {"name": "bremsstrahlung", "keywords": ["BREMSSTRAHLUNG"], "fluxes": "709", "post": add_response,
         "deck": keyed("bremsstrahlung", "P138 BREMSSTRAHLUNG", before_flux=[f"BREMSSTRAHLUNG 1 {BREMS_NUCLIDE.upper()}"]),
         "hands": {"nuclides": (hand.photon("P138 hand BREMSSTRAHLUNG",
                                            {"bremsstrahlung": {"nuclides": [BREMS_NUCLIDE]}}, response), no_echo)}},
        {"name": "path", "keywords": ["PATH"], "fluxes": "709",
         "deck": keyed("path", "P138 PATH", before_flux=[f"PATH 1 {PATH_TARGET.upper()}"]),
         "hands": {"pathways_targets": (hand.pathways("P138 hand PATH"), no_echo)}},
        {"name": "atwo", "keywords": ["ATWO"], "fluxes": "709",
         "deck": keyed("atwo", "P138 ATWO", before_flux=["ATWO"]),
         "hands": {"a2_table": (hand.atwo("P138 hand ATWO"), no_echo)}},
        {"name": "power", "keywords": ["POWER"], "fluxes": "709", "post": add_kerma,
         "deck": keyed("power", "P138 POWER", flux_line=f"POWER {POWER_W_CM3:g} 1 301"),
         "hands": {"power_with_kerma": (hand.power("P138 hand POWER", kerma_ref), no_echo)}},
        # report-only extras
        {"name": "cnvtype1", "keywords": ["GRPCONVERT", "CNVTYPE"], "fluxes": "175", "gating": False,
         "deck": keyed("cnvtype1", "P138 CNVTYPE 1", before_flux=["GRPCONVERT 175 709", "CNVTYPE 1"]),
         "hands": {"rebin_equal_energy": (hand.rebin("P138 hand CNVTYPE 1", values175_read, b175, "equal_energy"),
                                          no_echo)}},
        {"name": "limgrp", "keywords": ["GRPCONVERT", "LIMGRP"], "fluxes": "175", "gating": False,
         "deck": keyed("limgrp", "P138 LIMGRP",
                       before_flux=["GRPCONVERT 175 709", f"LIMGRP {LIMGRP_EV[0]:g} {LIMGRP_EV[1]:g}"]),
         "hands": {"rebin_limits_in_eV": (hand.rebin("P138 hand LIMGRP", values175_read, b175, "equal_lethargy",
                                                     LIMGRP_EV), no_echo)}},
    ]

    work = Work(candidate, run_dir, args.timeout)
    baseline = work.run("base_hand", hand.base("P138 hand base"))
    g3 = {"base_run_ok": baseline is not None, "variants": {}, "ambiguities": AMBIGUITIES,
          "tolerance": {"float_relative": REL_TOL, "echo_paths": ECHOES, "wall_twin_echo_paths": WALL_TWIN_ECHOES},
          "inputs": {"deck": record_file(deck_path), "fluxes": record_file(fluxes_path),
                     "fluxes_175": record_file(fluxes_175), "fluxes_50": record_file(fluxes_50),
                     "kerma_table": kerma_ref, "gate_spec": record_file(GATE_SPEC),
                     "photon_response": response or "not installed: the photon variants run without one",
                     "flux_total": flux_total, "density_g_cm3": density, "cooling_s": cooling,
                     "wall_loading_MW_m2_of_file": wall709}}
    if baseline is not None:
        for variant in variants:
            g3["variants"][variant["name"]] = run_variant(work, variant, baseline, fluxes_for)
    gating = [v for v in g3["variants"].values() if v["gating"]]
    g3["pass"] = bool(baseline is not None and len(gating) == sum(1 for v in variants if v.get("gating", True))
                      and all(v["pass"] for v in gating))
    gates["G3"] = g3

    # ---- G4: statuses against the equivalence results
    violations, unreasoned, table = [], [], {}
    for name, v in g3["variants"].items():
        table[name] = [{"keyword": r["keyword"], "line": r["line"], "status": r["status"]} for r in v["statuses"]]
        for r in v["statuses"]:
            if r["status"] not in STATUSES:
                unreasoned.append({"variant": name, "keyword": r["keyword"], "status": r["status"]})
            elif r["status"] != "mapped" and not r["detail"].strip():
                unreasoned.append({"variant": name, "keyword": r["keyword"], "status": r["status"]})
            if r["status"] == "mapped" and not v["pass"]:
                violations.append({"variant": name, "keyword": r["keyword"], "line": r["line"],
                                   "differences": {k: c.get("other", c.get("error")) for k, c in v["comparisons"].items()
                                                   if not c.get("identical_apart_from_echoes")}})
    negatives = negative_controls(work, deck_text, fluxes_path, fluxes_175, fluxes_50)
    g4 = {"statuses": table, "mapped_but_differs": violations[:MAX_LISTED], "missing_reason": unreasoned[:MAX_LISTED],
          "variants_missing": [v["name"] for v in variants if v["name"] not in g3["variants"]],
          "negative_controls": negatives}
    g4["pass"] = bool(g3["variants"] and not violations and not unreasoned and not g4["variants_missing"]
                      and all(n["pass"] for n in negatives))
    gates["G4"] = g4

    verdict = all(bool(gates[g].get("pass")) for g in ("G2", "G3", "G4"))
    record = {"protocol": "protocols/ACTINV-P138_PROTOCOL.md", "protocol_sha256": sha256_file(PROTOCOL),
              "candidate_sha256": sha256_file(candidate), "checker_sha256": sha256_file(__file__),
              "workdir": str(run_dir), "runs": work.runs, "gates": gates, "verdict": "PASS" if verdict else "FAIL"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in ("G2", "G3", "G4"):
        print(f"P138 {gate}: {'PASS' if gates[gate].get('pass') else 'FAIL'}")
    for name, v in g3["variants"].items():
        mode = "" if v["gating"] else " (report only)"
        status = ",".join(f"{r['keyword']}={r['status']}" for r in v["statuses"])
        print(f"  G3/{name}{mode}: {'ok' if v['pass'] else 'DIFFERS'}  [{status}]")
    for n in negatives:
        print(f"  G4/{n['name']}: {'ok' if n['pass'] else 'FAIL'}")
    print(f"P138: {record['verdict']} ({out_path})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
