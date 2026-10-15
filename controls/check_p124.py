#!/usr/bin/env python3
"""P124 gates G2-G7 (protocols/ACTINV-P124_PROTOCOL.md): input parity.

  G2  The unchanged FNS Fe spec gives results from the reference and candidate binaries that are identical apart
      from the top-level `ms` (parsed-JSON equality, plus a byte comparison with the `ms` value masked).
  G3  `spectrum.rebin`. The FNS Fe spectrum (709 groups) is aggregated by this script to the 175-group VITAMIN-J
      structure (equal-lethargy split), and the run converts it back to 709 groups, for both conventions.
      The ledger `spectrum_conversion` record is compared with this script's own implementation of both conventions
      (totals, flux moved by the limits, flux outside the destination, residual <= 1e-12), and flux is conserved
      across the two conversions. The ledger carries no per-group flux, so the per-group content of the converted
      spectrum is checked indirectly: the candidate is run again with this script's own converted 709-group spectrum
      supplied explicitly (no rebin) and the two inventories must agree (PROXY_REL_TOL on nuclides above 1e-15 of
      total). The ratio table against the native-709 run, and a LIMGRP (emin/emax) variant, are report-only.
  G4  `wall_loading_MW_m2`, both bases (a declared `spectrum.wall_loading_MW_m2` reference, and the 4.44e13 n cm-2 s-1
      per MW m-2 equivalence). The step multiplier recorded in the ledger is checked against this script's own
      arithmetic, and the run is compared with the same spec carrying that multiplier as an explicit `flux`; the
      results must be identical apart from `ms` and the ledger `wall_loading` block.
  G5  `partition {Mn: 0.25}` on a zero-duration step after the FNS irradiation step: every Mn nuclide is exactly
      0.25 x its value before (1e-14), every other nuclide is unchanged (1e-14), the removal sink grows by the removed
      atoms, the mode is coupled, and an explicit trace mode is refused.
  G6  `overrides`. Half-life of Mn56 equal to the decay-file value (this script's own MT=457 parse): identical apart
      from echoes. Half-life halved: lambda doubles and Mn56 atoms and activity follow a single-nuclide production
      and decay recomputation (1e-9). Cross section of Fe56 MT=103 equal to the `inspect xs` one-group value:
      identical apart from echoes. Scale x2: Mn56 follows the same recomputation with the Fe56(n,p)Mn56 production
      rate doubled, the rate built from the library NPZ (streamed rows of sig.npy) (1e-9).
  G7  Decay-only: the FNS Fe step-1 inventory as `atoms_per_g`, the cooling steps only, no library and no spectrum,
      is accepted and its inventory equals the same cooling run through the full spec (1e-12 relative, nuclides
      above 1e-15 of total).

G1 (fmt, clippy, workspace tests) and G8 (CI) are not checked here. Overall PASS iff G2-G7 pass. Only G2 uses the
reference binary: it predates every P124 field. Runs are serial, without a shell, each under a timeout, from the
repository root (the spec paths are relative to it).

Usage:
  check_p124.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p124-work]
      [--out results/p124_inputs.json] [--timeout 3600] [--reuse]

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on a missing input.
"""
import argparse
import bisect
import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P124_PROTOCOL.md"
TABLES_RS = ROOT / "crates" / "actinv-data" / "src" / "tables.rs"
GROUPS_709 = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
GROUPS_175 = ROOT / "crates" / "actinv-data" / "data" / "fispact_175_groups.json"

AVOGADRO = 6.02214076e23
WALL_CURRENT_PER_MW_M2 = 4.44e13  # FISPACT-II manual 4.91: 4.44e13 n cm-2 s-1 is equivalent to 1 MW m-2
LN2 = math.log(2.0)

CONSERVATION_TOL = 1e-12
CONVERSION_TOL = 1e-12
PROXY_REL_TOL = 1e-9  # inventory of the rebin run against the explicit-spectrum run (see G3)
PARTITION_TOL = 1e-14
OVERRIDE_TOL = 1e-9
DECAY_ONLY_TOL = 1e-12
SMALL_FRACTION = 1e-15  # nuclides below this fraction of the step's total atoms are not compared
MAX_LISTED = 25

# Echoes that the protocol allows to differ between a run and its explicit-parameter twin.
ALLOWED_WALL = ["/ms", "/ledger/wall_loading", "/certificate/wall_loading"]
ALLOWED_OVERRIDES = ["/ms", "/ledger/overrides", "/certificate/overrides"]


# ------------------------------------------------------------------------------ generic helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def canonical(obj):
    return json.dumps(obj, sort_keys=True)


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def short(value):
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def json_diffs(a, b, path="", out=None, limit=400):
    """Every differing JSON path (a key present on one side only is one difference)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({"path": f"{path}/{key}", "a": "<absent>" if key not in a else short(a[key]),
                            "b": "<absent>" if key not in b else short(b[key])})
            else:
                json_diffs(a[key], b[key], f"{path}/{key}", out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "a": f"length {len(a)}", "b": f"length {len(b)}"})
        for i, (x, y) in enumerate(zip(a, b)):
            json_diffs(x, y, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                break
    elif a != b or type(a) is not type(b):
        out.append({"path": path, "a": short(a), "b": short(b)})
    return out


def is_allowed(path, allowed):
    return any(path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "[") for prefix in allowed)


def compare_results(a, b, allowed):
    """Compare two results. Returns the gate fragment: differences split into allowed echoes and the rest."""
    diffs = json_diffs(a, b)
    allowed_diffs = [d for d in diffs if is_allowed(d["path"], allowed)]
    other = [d for d in diffs if not is_allowed(d["path"], allowed)]
    return {"identical_apart_from_echoes": not other, "allowed_echo_paths": sorted({d["path"] for d in allowed_diffs}),
            "n_other_differences": len(other), "other_differences": other[:MAX_LISTED],
            "differences_truncated": len(diffs) >= 400}


class Checks:
    """Comparison collector: counts, worst relative error per quantity, and a capped list of mismatches."""

    def __init__(self):
        self.n = 0
        self.bad = []
        self.n_bad = 0
        self.worst = {}

    def close(self, name, where, got, want, tol):
        self.n += 1
        if got is None or want is None:
            ok = got is None and want is None
            err = 0.0 if ok else math.inf
        else:
            err = rel_diff(got, want)
            ok = err <= tol
        self.worst[name] = max(self.worst.get(name, 0.0), err)
        if not ok:
            self.n_bad += 1
            if len(self.bad) < MAX_LISTED:
                self.bad.append({"quantity": name, "where": where, "got": got, "expected": want,
                                 "relative_difference": err, "tolerance": tol})
        return ok

    def equal(self, name, where, got, want):
        self.n += 1
        ok = got == want
        if not ok:
            self.n_bad += 1
            if len(self.bad) < MAX_LISTED:
                self.bad.append({"quantity": name, "where": where, "got": got, "expected": want})
        return ok

    def report(self):
        return {"n_comparisons": self.n, "n_mismatches": self.n_bad, "worst_relative_difference": self.worst,
                "mismatches": self.bad}


def parse_duration_s(text):
    """Seconds in a schedule `dt` such as "300.0 s", "5 min", "0.3156 d"."""
    m = re.fullmatch(r"\s*([-+0-9.eE]+?)\s*([A-Za-z]*)\s*", text)
    if not m:
        raise ValueError(f"bad duration {text!r}")
    unit = {"": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0, "min": 60.0, "m": 60.0, "h": 3600.0, "hr": 3600.0,
            "d": 86400.0, "y": 365.25 * 86400.0, "yr": 365.25 * 86400.0}[m.group(2).lower()]
    return float(m.group(1)) * unit


# ------------------------------------------------------------------------------ ENDF-6 MF=8 MT=457 half-lives


def endf_float(field):
    """ENDF-6 real: blank is zero, `1.234567+3` means 1.234567e3."""
    text = field.strip()
    if not text:
        return 0.0
    for i in range(len(text) - 1, 0, -1):
        if text[i] in "+-" and text[i - 1] not in "eE":
            text = text[:i] + "e" + text[i:]
            break
    return float(text)


def scan_half_lives(path, wanted):
    """Half-life (and NST) of the (ZA, LISO) keys in `wanted`, plus every key in the file. A section is the run of
    consecutive lines tagged MF=8 MT=457 under one MAT; its first line is the HEAD, its second the first LIST
    record whose C1 is the half-life."""
    found, keys = {}, set()
    cur_mat, sec_line, head = None, 0, None
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            if line[70:75] == " 8457":
                mat = line[66:70]
                if mat != cur_mat:
                    cur_mat, sec_line = mat, 0
                if sec_line == 0:
                    head = (int(round(endf_float(line[0:11]))), int(line[33:44].strip() or 0),
                            int(line[44:55].strip() or 0))
                    keys.add((head[0], head[1]))
                elif sec_line == 1 and (head[0], head[1]) in wanted:
                    found[(head[0], head[1])] = {"half_life_s": endf_float(line[0:11]), "nst": head[2]}
                sec_line += 1
            else:
                cur_mat = None
    return found, keys


def decay_half_life(primary, fallback, key):
    """Half-life as the engine merges the files: the primary wins, the fallback fills keys the primary lacks."""
    found, _ = scan_half_lives(primary, {key})
    if key in found:
        return found[key], "primary"
    if fallback:
        found, _ = scan_half_lives(fallback, {key})
        if key in found:
            return found[key], "fallback"
    raise ValueError(f"no decay record for {key} in the decay files")


# ------------------------------------------------------------------------------ spectra and conversions


def ascending_flux(spectrum):
    """Group flux in ascending-energy order, scaled to `total` when given (every flux times total/sum)."""
    f = [float(v) for v in spectrum["flux_per_group"]]
    if spectrum.get("descending", False):
        f.reverse()
    total = spectrum.get("total")
    if total is not None:
        s = math.fsum(f)
        if s > 0.0:
            k = total / s
            f = [v * k for v in f]
    return f


def group_fraction(convention, lo, hi, a, b):
    """Share of source group [lo, hi] that falls in [a, b] when flux is uniform in lethargy or in energy."""
    if convention == "equal_lethargy":
        return math.log(b / a) / math.log(hi / lo)
    return (b - a) / (hi - lo)


def rebin(sb, sf, db, convention):
    """Distribute source group fluxes onto the destination boundaries. Returns (flux, below, above, exact_grid)."""
    if sb == db:
        return list(sf), 0.0, 0.0, True
    parts = [[] for _ in range(len(db) - 1)]
    below, above = [], []
    t_lo, t_hi = db[0], db[-1]
    for g, flux in enumerate(sf):
        if flux == 0.0:
            continue
        lo, hi = sb[g], sb[g + 1]
        if lo < t_lo:
            below.append(flux * group_fraction(convention, lo, hi, lo, min(hi, t_lo)))
        if hi > t_hi:
            above.append(flux * group_fraction(convention, lo, hi, max(lo, t_hi), hi))
        d = max(bisect.bisect_right(db, lo) - 1, 0)
        while d < len(parts):
            a, b = max(lo, db[d]), min(hi, db[d + 1])
            if b > a:
                parts[d].append(flux * group_fraction(convention, lo, hi, a, b))
            if db[d + 1] >= hi:
                break
            d += 1
    return [math.fsum(p) for p in parts], math.fsum(below), math.fsum(above), False


def clip_range(sb, sf, emin, emax):
    """LIMGRP as docs/guide/specification.md describes it: flux in source groups wholly below `emin` is folded into
    the group that contains it (whose lower edge becomes `emin`); likewise above `emax`."""
    b, f = list(sb), list(sf)
    moved_below = moved_above = 0.0
    if emin is not None and emin > b[0]:
        g = bisect.bisect_right(b, emin) - 1
        moved_below = math.fsum(f[:g])
        f, b = f[g:], b[g:]
        f[0] += moved_below
        b[0] = emin
    if emax is not None and emax < b[-1]:
        end = bisect.bisect_left(b, emax)
        moved_above = math.fsum(f[end:])
        f, b = f[:end], b[:end + 1]
        f[end - 1] += moved_above
        b[end] = emax
    return b, f, moved_below, moved_above


def load_boundaries(path):
    data = json.loads(path.read_text())
    ascending = sorted(float(v) for v in data["boundaries_eV"])
    if any(y <= x for x, y in zip(ascending, ascending[1:])):
        raise ValueError(f"{path} boundaries are not strictly increasing")
    return ascending


# ------------------------------------------------------------------------------ library NPZ


def read_exact(fp, n):
    chunks = []
    while n > 0:
        block = fp.read(n)
        if not block:
            raise ValueError("sig.npy ended early")
        chunks.append(block)
        n -= len(block)
    return b"".join(chunks)


def npz_sig_rows(npz_path, wanted, ngroups, nrows):
    """sig[row, :] for the sorted row indices `wanted`, streamed from the NPZ member (the full array is ~1 GB)."""
    with zipfile.ZipFile(npz_path) as zf:
        with zf.open("sig.npy") as fp:
            version = np.lib.format.read_magic(fp)
            reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
            shape, fortran, dtype = reader(fp)
            if fortran or dtype != np.dtype("<f8") or shape != (nrows, ngroups):
                raise ValueError(f"sig.npy is {shape} {dtype} fortran={fortran}; expected ({nrows}, {ngroups}) <f8")
            row_bytes = ngroups * 8
            base = fp.tell()
            out = np.empty((len(wanted), ngroups), dtype="<f8")
            k = 0
            while k < len(wanted):
                run = 1
                while k + run < len(wanted) and wanted[k + run] == wanted[k] + run:
                    run += 1
                fp.seek(base + int(wanted[k]) * row_bytes)
                out[k:k + run] = np.frombuffer(read_exact(fp, run * row_bytes), dtype="<f8").reshape(run, ngroups)
                k += run
    return out


def collapse(sig_row, phi):
    """sum(sigma_g phi_g) / sum(phi_g) with exact summation."""
    den = math.fsum(phi)
    return math.fsum((sig_row * np.array(phi)).tolist()) / den if den > 0.0 else 0.0


def fe_isotopes():
    """(A, abundance, mass u) of the Fe rows in crates/actinv-data/src/tables.rs."""
    pattern = re.compile(r'\("Fe",\s*(\d+),\s*0,\s*([0-9.eE+-]+),\s*([0-9.eE+-]+)\)')
    rows = [(int(a), float(ab), float(m)) for a, ab, m in pattern.findall(TABLES_RS.read_text())]
    if not rows:
        raise ValueError("no Fe rows found in tables.rs")
    return rows


# ------------------------------------------------------------------------------ result accessors


def inventory(step):
    return {row["nuclide"]: row["atoms_per_g"] for row in step["inventory"]}


def compare_inventories(res_a, res_b, rel_tol):
    """Step-by-step inventory comparison over the nuclides above SMALL_FRACTION of the step's total (either run)."""
    checks = Checks()
    steps_a, steps_b = res_a["steps"], res_b["steps"]
    checks.equal("steps", "", len(steps_a), len(steps_b))
    for i, (sa, sb) in enumerate(zip(steps_a, steps_b)):
        inv_a, inv_b = inventory(sa), inventory(sb)
        total = max(math.fsum(inv_a.values()), math.fsum(inv_b.values()))
        for name in sorted(set(inv_a) | set(inv_b)):
            a, b = inv_a.get(name, 0.0), inv_b.get(name, 0.0)
            if max(a, b) <= SMALL_FRACTION * total:
                continue
            checks.close("inventory_atoms_per_g", f"step{i + 1}/{name}", a, b, rel_tol)
        checks.close("total_activity_Bq_per_g", f"step{i + 1}", math.fsum(sa["activity_Bq_per_g"].values()),
                     math.fsum(sb["activity_Bq_per_g"].values()), rel_tol)
    return checks


# ------------------------------------------------------------------------------ running the binaries


class Runner:
    def __init__(self, workdir, timeout, reuse, record):
        self.workdir, self.timeout, self.reuse, self.record = workdir, timeout, reuse, record

    def run(self, label, binary, spec, expect_failure=False):
        """Run `binary run SPEC OUT` from the repository root. `spec` is a dict (written to the workdir) or a
        path. Returns the parsed result, or None when the run failed (the failure is in the run record). With
        expect_failure the result is always None and the record carries the exit code and the log tail."""
        if isinstance(spec, dict):
            spec_path = self.workdir / f"{label}.spec.json"
            spec_path.write_text(json.dumps(spec, indent=1) + "\n")
        else:
            spec_path = Path(spec)
        out_file, log_file = self.workdir / f"{label}.json", self.workdir / f"{label}.log"
        info = {"binary": str(binary), "spec": record_file(spec_path), "cwd": str(ROOT)}
        self.record["runs"][label] = info
        if self.reuse and not expect_failure and out_file.exists():
            info["reused"] = True
        else:
            started = time.monotonic()
            try:
                with open(log_file, "wb") as log:
                    proc = subprocess.run([str(binary), "run", str(spec_path), str(out_file)], cwd=str(ROOT),
                                          stdout=log, stderr=subprocess.STDOUT, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                info["error"] = f"timed out after {self.timeout} s"
                return None
            except OSError as error:
                info["error"] = f"{type(error).__name__}: {error}"
                return None
            info["exit_code"] = proc.returncode
            info["wall_s"] = time.monotonic() - started
            if expect_failure:
                info["log_tail"] = log_file.read_text(errors="replace")[-600:]
                return None
            if proc.returncode != 0:
                info["error"] = f"exit code {proc.returncode}; see {log_file}"
                info["log_tail"] = log_file.read_text(errors="replace")[-600:]
                return None
        info["output"] = record_file(out_file)
        return json.loads(out_file.read_text())

    def inspect(self, label, binary, args):
        """`binary inspect ARGS...`, stdout parsed as JSON; returns None on failure."""
        info = {"binary": str(binary), "argv": [str(a) for a in args], "cwd": str(ROOT)}
        self.record["runs"][label] = info
        try:
            proc = subprocess.run([str(binary), "inspect", *[str(a) for a in args]], cwd=str(ROOT),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout)
        except (subprocess.TimeoutExpired, OSError) as error:
            info["error"] = f"{type(error).__name__}: {error}"
            return None
        info["exit_code"] = proc.returncode
        if proc.returncode != 0:
            info["error"] = proc.stderr.decode(errors="replace")[-400:]
            return None
        (self.workdir / f"{label}.json").write_bytes(proc.stdout)
        info["output"] = record_file(self.workdir / f"{label}.json")
        return json.loads(proc.stdout)


# ------------------------------------------------------------------------------ gates


def gate_g2(ctx):
    ref, cand = ctx["ref_result"], ctx["base_result"]
    if ref is None or cand is None:
        return {"pass": False, "error": "a gate run failed; see runs"}
    ref_s = {k: v for k, v in ref.items() if k != "ms"}
    cand_s = {k: v for k, v in cand.items() if k != "ms"}
    equal = canonical(ref_s) == canonical(cand_s)
    mask = re.compile(r'"ms"\s*:\s*[-+0-9.eE]+')
    ref_raw = mask.sub('"ms":0', (ctx["workdir"] / "g2_reference.json").read_text())
    cand_raw = mask.sub('"ms":0', (ctx["workdir"] / "g2_candidate.json").read_text())
    out = {"canonical_equal_apart_from_ms": equal, "bytes_equal_with_ms_masked": ref_raw == cand_raw,
           "steps": len(ref.get("steps", [])), "gate_spec_sha256": sha256_file(GATE_SPEC)}
    if not equal:
        out["first_differences"] = json_diffs(ref_s, cand_s, limit=MAX_LISTED)
    out["pass"] = bool(equal)
    return out


def gate_g3(ctx):
    run, cand, base_spec = ctx["run"], ctx["candidate"], ctx["base_spec"]
    b709, b175 = load_boundaries(GROUPS_709), load_boundaries(GROUPS_175)
    out = {"vendored_groups": {"fispact-709": record_file(GROUPS_709), "fispact-175": record_file(GROUPS_175)}}
    with np.load(ctx["library"]) as z:
        out["vendored_709_equals_library_bounds"] = [float(v) for v in z["bounds"]] == b709
    flux709 = ascending_flux(base_spec["spectrum"])
    total709 = math.fsum(flux709)
    flux175, lost_below, lost_above, _ = rebin(b709, flux709, b175, "equal_lethargy")
    out["aggregation_709_to_175"] = {"convention": "equal_lethargy", "source_total": total709,
                                    "aggregated_total": math.fsum(flux175), "outside_175_below": lost_below,
                                    "outside_175_above": lost_above,
                                    "residual": rel_diff(total709, math.fsum(flux175) + lost_below + lost_above)}
    native = ctx["base_result"]
    variants = {"equal_lethargy": ("equal_lethargy", None, None), "equal_energy": ("equal_energy", None, None),
                "limits": ("equal_lethargy", 1.0, 1.0e7)}
    results, gating_ok = {}, True
    for name, (convention, emin, emax) in variants.items():
        report_only = name == "limits"
        rebin_block = {"convention": convention}
        if emin is not None:
            rebin_block["emin_eV"] = emin
        if emax is not None:
            rebin_block["emax_eV"] = emax
        clipped_b, clipped_f, moved_below, moved_above = clip_range(b175, flux175, emin, emax)
        mine, outside_below, outside_above, exact = rebin(clipped_b, clipped_f, b709, convention)
        spec = copy.deepcopy(base_spec)
        spec["spectrum"] = {"structure": "fispact-709", "boundaries_eV": b175, "flux_per_group": flux175,
                            "descending": False, "rebin": rebin_block}
        explicit = copy.deepcopy(base_spec)
        explicit["spectrum"] = {"structure": "fispact-709", "flux_per_group": mine, "descending": False}
        res = run.run(f"g3_rebin_{name}", ctx["candidate"], spec)
        twin = run.run(f"g3_explicit_{name}", ctx["candidate"], explicit)
        block = {"report_only": report_only, "rebin": rebin_block}
        if res is None or twin is None:
            block.update({"pass": False, "error": "a run failed; see runs"})
            results[name] = block
            gating_ok = gating_ok and report_only
            continue
        checks = Checks()
        records = res["ledger"].get("spectrum_conversion") or []
        checks.equal("ledger.spectrum_conversion records", name, len(records), 1)
        if records:
            rec = records[0]
            checks.equal("convention", name, rec.get("convention"), convention)
            checks.equal("cnvtype", name, rec.get("cnvtype"), 1 if convention == "equal_energy" else 0)
            checks.equal("destination_structure", name, rec.get("destination_structure"), "fispact-709")
            checks.equal("source_groups", name, rec.get("source_groups"), len(flux175))
            checks.equal("destination_groups", name, rec.get("destination_groups"), len(mine))
            checks.equal("exact_grid", name, rec.get("exact_grid"), exact)
            checks.equal("emin_eV", name, rec.get("emin_eV"), emin)
            checks.equal("emax_eV", name, rec.get("emax_eV"), emax)
            for field, want in (("limit_flux_moved_from_below", moved_below),
                                ("limit_flux_moved_from_above", moved_above),
                                ("source_total", math.fsum(clipped_f)), ("destination_total", math.fsum(mine)),
                                ("outside_destination_below", outside_below),
                                ("outside_destination_above", outside_above)):
                got = rec.get(field)
                if got is None:
                    checks.equal(field, name, got, want)
                else:
                    checks.close(field, name, got, want, CONVERSION_TOL)
            residual = rec.get("flux_conservation_residual")
            checks.equal("flux_conservation_residual <= 1e-12", name,
                         residual is not None and residual <= CONSERVATION_TOL, True)
        assembly = res["ledger"].get("assembly", {})
        checks.close("assembly.flux_total", name, assembly.get("flux_total"), math.fsum(mine), CONVERSION_TOL)
        accounted = math.fsum(mine) + outside_below + outside_above
        conserved = rel_diff(math.fsum(clipped_f), accounted)
        checks.equal("flux conserved by the conversion (mine) <= 1e-12", name, conserved <= CONSERVATION_TOL, True)
        proxy = compare_inventories(res, twin, PROXY_REL_TOL)
        block.update({
            "conversion_checks": checks.report(),
            "explicit_spectrum_proxy": {"tolerance": PROXY_REL_TOL, **proxy.report()},
            "my_conversion": {"destination_total": math.fsum(mine), "outside_below": outside_below,
                              "outside_above": outside_above, "moved_below": moved_below,
                              "moved_above": moved_above, "conservation_residual": conserved},
            "ledger_record": records[0] if records else None,
        })
        if name == "equal_lethargy":
            total_chain = math.fsum(mine) + outside_below + outside_above + lost_below + lost_above
            block["flux_conserved_across_both_conversions"] = {
                "original_709_total": total709, "after_round_trip_plus_outside": total_chain,
                "relative_difference": rel_diff(total709, total_chain),
                "within_1e-12": rel_diff(total709, total_chain) <= CONSERVATION_TOL}
            checks_chain = rel_diff(total709, total_chain) <= CONSERVATION_TOL
        else:
            checks_chain = True
        block["pass"] = bool(checks.n_bad == 0 and proxy.n_bad == 0 and checks_chain)
        # report-only: the round trip against the native-709 run
        if native is not None:
            ratios = []
            for i, (s_r, s_n) in enumerate(zip(res["steps"], native["steps"])):
                a, n_ = math.fsum(s_r["activity_Bq_per_g"].values()), math.fsum(s_n["activity_Bq_per_g"].values())
                ratios.append({"step": i + 1, "activity_rebin": a, "activity_native": n_,
                               "ratio": a / n_ if n_ > 0.0 else None})
            inv_r, inv_n = inventory(res["steps"][0]), inventory(native["steps"][0])
            total_n = math.fsum(inv_n.values())
            nuclide_ratios = sorted(
                ({"nuclide": k, "ratio": inv_r.get(k, 0.0) / v} for k, v in inv_n.items()
                 if v > 1e-12 * total_n and v > 0.0), key=lambda e: e["ratio"])
            block["native_709_comparison_report_only"] = {
                "activity_by_step": ratios,
                "step1_nuclide_ratio_min": nuclide_ratios[:3], "step1_nuclide_ratio_max": nuclide_ratios[-3:],
                "step1_nuclides_compared": len(nuclide_ratios)}
        results[name] = block
        if not report_only:
            gating_ok = gating_ok and block["pass"]
    out["variants"] = results
    out["pass"] = bool(gating_ok and out["aggregation_709_to_175"]["residual"] <= CONSERVATION_TOL)
    return out


def wall_schedule(base_spec, walls):
    """The FNS schedule with step 1 and a second irradiation step (after the 66 s cooling step) given as wall
    loadings (a placeholder dict; the caller sets the field)."""
    steps = copy.deepcopy(base_spec["schedule"])
    return [{"dt": steps[0]["dt"], "wall": walls[0]}, steps[1], {"dt": "60 s", "wall": walls[1]}, *steps[2:]]


def gate_g4(ctx):
    run, cand, base_spec = ctx["run"], ctx["candidate"], ctx["base_spec"]
    phi = ascending_flux(base_spec["spectrum"])
    total = math.fsum(phi)
    bases = {"spectrum_reference": {"reference": 0.6, "walls": [0.3, 0.1]},
             "14_MeV_equivalence": {"reference": None, "walls": [1.0e-3, 4.0e-4]}}
    out, ok_all = {"spectrum_total_cm2_s": total, "bases": {}}, True
    for name, cfg in bases.items():
        schedule = wall_schedule(base_spec, cfg["walls"])
        wall_steps = [{"dt": s["dt"], "wall_loading_MW_m2": s["wall"]} if "wall" in s else s for s in schedule]
        spec = copy.deepcopy(base_spec)
        spec["schedule"] = wall_steps
        if cfg["reference"] is not None:
            spec["spectrum"]["wall_loading_MW_m2"] = cfg["reference"]
        res = run.run(f"g4_wall_{name}", cand, spec)
        block = {"reference_wall_loading_MW_m2": cfg["reference"], "walls_MW_m2": cfg["walls"]}
        if res is None:
            block.update({"pass": False, "error": "the wall-loading run failed; see runs"})
            out["bases"][name] = block
            ok_all = False
            continue
        records = res["ledger"].get("wall_loading") or []
        checks = Checks()
        checks.equal("wall_loading records", name, len(records), len(cfg["walls"]))
        multipliers = []
        for rec, wall in zip(records, cfg["walls"]):
            if cfg["reference"] is not None:
                want = wall / cfg["reference"]
                checks.equal("basis", name, rec.get("basis"), "spectrum_reference_wall_loading")
                checks.equal("multiplier == wall / reference (bitwise)", name, rec.get("flux_multiplier"), want)
                checks.equal("reference_wall_loading_MW_m2", name, rec.get("reference_wall_loading_MW_m2"),
                             cfg["reference"])
            else:
                want = wall * WALL_CURRENT_PER_MW_M2 / total
                checks.equal("basis", name, rec.get("basis"), "14_MeV_current_equivalence")
                checks.close("multiplier = wall 4.44e13 / total", name, rec.get("flux_multiplier"), want,
                             CONVERSION_TOL)
                checks.equal("current_per_MW_m2_cm2_s", name, rec.get("current_per_MW_m2_cm2_s"),
                             WALL_CURRENT_PER_MW_M2)
            checks.equal("wall_loading_MW_m2", name, rec.get("wall_loading_MW_m2"), wall)
            if rec.get("flux_multiplier") is not None:
                checks.close("step_total_flux = multiplier x total", name, rec.get("step_total_flux_cm2_s"),
                             rec["flux_multiplier"] * total, CONVERSION_TOL)
            multipliers.append(rec.get("flux_multiplier"))
        block["ledger_checks"] = checks.report()
        block["ledger_records"] = records
        if len(multipliers) != len(cfg["walls"]) or any(m is None for m in multipliers):
            block.update({"pass": False, "error": "no usable multipliers in the ledger"})
            out["bases"][name] = block
            ok_all = False
            continue
        explicit = copy.deepcopy(base_spec)
        it = iter(multipliers)
        explicit["schedule"] = [{"dt": s["dt"], "flux": next(it)} if "wall" in s else s for s in schedule]
        twin = run.run(f"g4_flux_{name}", cand, explicit)
        if twin is None:
            block.update({"pass": False, "error": "the explicit-flux run failed; see runs"})
            out["bases"][name] = block
            ok_all = False
            continue
        block["comparison"] = compare_results(res, twin, ALLOWED_WALL)
        block["multipliers_used"] = multipliers
        block["pass"] = bool(checks.n_bad == 0 and block["comparison"]["identical_apart_from_echoes"])
        ok_all = ok_all and block["pass"]
        out["bases"][name] = block
    out["pass"] = bool(ok_all)
    return out


def gate_g5(ctx):
    run, cand, base_spec = ctx["run"], ctx["candidate"], ctx["base_spec"]
    kept = 0.25
    spec = copy.deepcopy(base_spec)
    spec["schedule"] = [base_spec["schedule"][0], {"dt": "0 s", "flux": 0.0, "partition": {"Mn": kept}}]
    res = run.run("g5_partition", cand, spec)
    refusal_spec = copy.deepcopy(spec)
    refusal_spec.setdefault("options", {})["mode"] = "trace"
    run.run("g5_partition_trace", cand, refusal_spec, expect_failure=True)
    refusal = ctx["record"]["runs"]["g5_partition_trace"]
    out = {"fraction_kept": kept, "trace_mode_refused": {
        "exit_code": refusal.get("exit_code"), "refused": refusal.get("exit_code") not in (None, 0),
        "log_tail": refusal.get("log_tail")}}
    if res is None:
        out.update({"pass": False, "error": "the partition run failed; see runs"})
        return out
    before, after = res["steps"][0], res["steps"][1]
    inv_b, inv_a = inventory(before), inventory(after)
    z_of = {row["nuclide"]: row["Z"] for step in res["steps"] for row in step["inventory"]}
    checks = Checks()
    removed_expected = []
    for name, atoms in inv_b.items():
        if z_of[name] == 25:
            checks.close("Mn atoms after = 0.25 x before", name, inv_a.get(name, 0.0), kept * atoms, PARTITION_TOL)
            removed_expected.append((1.0 - kept) * atoms)
        else:
            checks.close("other element unchanged", name, inv_a.get(name, 0.0), atoms, PARTITION_TOL)
    extra = sorted(set(inv_a) - set(inv_b))
    checks.equal("no nuclide appears after the partition", "", extra, [])
    mn = sorted(n for n in inv_b if z_of[n] == 25)
    checks.equal("Mn nuclides present before", "", len(mn) > 0, True)
    sink_before, sink_after = before.get("removed_atoms_per_g"), after.get("removed_atoms_per_g")
    removed = math.fsum(removed_expected)
    if sink_before is None or sink_after is None:
        checks.equal("removal sink reported", "", False, True)
    else:
        checks.close("sink growth = removed atoms", "", sink_after - sink_before, removed, PARTITION_TOL)
    ledger = res["ledger"].get("partition") or {}
    applied = ledger.get("applied") or []
    checks.equal("ledger applied records", "", len(applied), 1)
    if applied:
        checks.equal("ledger element", "", applied[0].get("element"), "Mn")
        checks.equal("ledger step", "", applied[0].get("step"), 2)
        checks.equal("ledger fraction_kept", "", applied[0].get("fraction_kept"), kept)
        checks.close("ledger removed_atoms_per_g", "", applied[0].get("removed_atoms_per_g"), removed, PARTITION_TOL)
    checks.equal("result mode is coupled", "", res.get("mode"), "coupled")
    checks.equal("ledger.partition.mode", "", ledger.get("mode"), "coupled")
    checks.equal("ledger.partition.mode_forced_coupled (declared auto)", "", ledger.get("mode_forced_coupled"), True)
    out.update({"checks": checks.report(), "mn_nuclides": mn, "removed_atoms_per_g_independent": removed,
                "sink_growth": None if sink_before is None or sink_after is None else sink_after - sink_before,
                "ledger_partition": ledger,
                "note": "the partition step lasts 0 s, so its output is the state just after the partition "
                        "(CRAM at dt=0 is a uniform factor within ~1e-15)"})
    out["pass"] = bool(checks.n_bad == 0 and out["trace_mode_refused"]["refused"])
    return out


def mn56_series(lam, production, steps):
    """Mn56 atoms at the end of each step: constant production `production x multiplier` and decay `lam`."""
    n, series = 0.0, []
    for dt, mult in steps:
        x = lam * dt
        n = n * math.exp(-x) + production * mult / lam * (-math.expm1(-x))
        series.append(n)
    return series


def mn56_of(result):
    return [inventory(s).get("Mn56") for s in result["steps"]], \
           [s["activity_Bq_per_g"].get("Mn56") for s in result["steps"]]


def gate_g6(ctx):
    run, cand, base_spec, base = ctx["run"], ctx["candidate"], ctx["base_spec"], ctx["base_result"]
    out = {}
    if base is None:
        return {"pass": False, "error": "the baseline FNS run failed"}
    hl_rec, hl_source = decay_half_life(ctx["decay_primary"], ctx["decay_fallback"], (25056, 0))
    hl = hl_rec["half_life_s"]
    lam = LN2 / hl
    out["mn56_decay_record"] = {"half_life_s": hl, "nst": hl_rec["nst"], "source": hl_source, "lambda_per_s": lam}
    steps = [(parse_duration_s(s["dt"]), s["flux"]) for s in base_spec["schedule"]]
    n_base, a_base = mn56_of(base)
    if any(v is None for v in n_base):
        return {**out, "pass": False, "error": "Mn56 is absent from a baseline step"}
    out["baseline_lambda_check"] = {"worst_relative_difference": max(rel_diff(a / n, lam)
                                                                     for a, n in zip(a_base, n_base))}
    # effective production rate (atoms g-1 s-1 per unit flux multiplier) from the baseline end of step 1
    dt0, m0 = steps[0]
    if not (m0 > 0.0 and all(m == 0.0 for _, m in steps[1:])):
        return {**out, "pass": False, "error": "the baseline schedule is not one irradiation step then cooling"}
    production = n_base[0] * lam / (m0 * -math.expm1(-lam * dt0))
    model = mn56_series(lam, production, steps)
    out["baseline_model_residual"] = {"worst_relative": max(rel_diff(m, n) for m, n in zip(model, n_base)),
                                      "production_atoms_per_g_per_s": production}

    # ---- half-life overrides
    def override_spec(items):
        spec = copy.deepcopy(base_spec)
        spec["overrides"] = items
        return spec

    sub = {}
    same = run.run("g6_half_life_same", cand, override_spec(
        [{"kind": "half_life", "nuclide": "Mn56", "value": hl, "units": "s"}]))
    sub["half_life_same"] = ({"pass": False, "error": "run failed; see runs"} if same is None else
                             {**compare_results(base, same, ALLOWED_OVERRIDES),
                              "ledger_overrides": same["ledger"].get("overrides")})
    if same is not None:
        sub["half_life_same"]["pass"] = sub["half_life_same"]["identical_apart_from_echoes"]
    half = run.run("g6_half_life_half", cand, override_spec(
        [{"kind": "half_life", "nuclide": "Mn56", "value": hl / 2.0, "units": "s"}]))
    if half is None:
        sub["half_life_halved"] = {"pass": False, "error": "run failed; see runs"}
    else:
        n_c, a_c = mn56_of(half)
        checks = Checks()
        if any(v is None for v in n_c):
            checks.equal("Mn56 present at every step", "", False, True)
        else:
            predicted = mn56_series(2.0 * lam, production, steps)
            for i, (n, a, p) in enumerate(zip(n_c, a_c, predicted)):
                checks.close("lambda = activity / atoms doubled", f"step{i + 1}", a / n, 2.0 * lam, OVERRIDE_TOL)
                checks.close("Mn56 atoms", f"step{i + 1}", n, p, OVERRIDE_TOL)
                checks.close("Mn56 activity", f"step{i + 1}", a, 2.0 * lam * p, OVERRIDE_TOL)
        sub["half_life_halved"] = {"checks": checks.report(), "ledger_overrides": half["ledger"].get("overrides"),
                                   "pass": checks.n_bad == 0}

    # ---- cross-section overrides
    library, ngroups = ctx["library"], ctx["library_groups"]
    with np.load(library) as z:
        rows, bounds = np.array(z["rows"]), np.array(z["bounds"])
    index = json.loads(Path(str(library)[:-4] + "_index.json").read_text())
    targets = [(t["za"], t["liso"]) for t in index["targets"]]
    fe56 = targets.index((26056, 0))
    sel = np.nonzero((rows[:, 0] == fe56) & (rows[:, 1] == 103))[0].tolist()
    sig = npz_sig_rows(library, sel, ngroups, int(rows.shape[0]))
    phi = ascending_flux(base_spec["spectrum"])
    flux_total = math.fsum(phi)
    loss = [k for k, r in enumerate(sel) if rows[r, 2] < 0]
    prod = [k for k, r in enumerate(sel) if rows[r, 2] == 25056 and rows[r, 3] == 0]
    if len(loss) != 1 or len(prod) != 1:
        return {**out, "sub": sub, "pass": False,
                "error": f"expected one loss row and one Mn56 product row for Fe56 MT=103, found {len(loss)}/{len(prod)}"}
    sigma_loss, sigma_np = collapse(sig[loss[0]], phi), collapse(sig[prod[0]], phi)
    isotopes = fe_isotopes()
    molar = sum(ab * m for _, ab, m in isotopes)
    ab56 = [ab for a, ab, _ in isotopes if a == 56][0]
    n_fe56 = AVOGADRO * ((100.0 / 100.0) / molar) * ab56
    rate_np = n_fe56 * sigma_np * 1e-24 * flux_total
    out["library_collapse"] = {"rows": [[int(v) for v in rows[r]] for r in sel], "sigma_loss_b": sigma_loss,
                               "sigma_Mn56_product_b": sigma_np, "total_flux_cm2_s": flux_total,
                               "fe56_atoms_per_g": n_fe56, "fe56_mass_table_molar_g_mol": molar,
                               "mn56_production_from_fe56_np_atoms_per_g_per_s": rate_np,
                               "baseline_effective_production_atoms_per_g_per_s": production,
                               "np_share_of_baseline_production": rate_np / production}
    inspected = run.inspect("g6_inspect_xs", cand, ["xs", ctx["library_ref"], "--target", "Fe56", "--mt", "103",
                                                    "--spectrum", ctx["spec_path"]])
    inspect_value = None
    if inspected is not None:
        losses = [r for r in inspected.get("reactions", []) if r.get("mt") == 103 and r.get("product_za", 0) < 0]
        if len(losses) == 1:
            inspect_value = losses[0].get("one_group_xs_b")
    out["inspect_one_group_xs_b"] = inspect_value
    out["inspect_vs_independent_collapse"] = None if inspect_value is None else rel_diff(inspect_value, sigma_loss)
    if inspect_value is None:
        sub["cross_section_same"] = {"pass": False, "error": "no one-group value from `inspect xs`; see runs"}
    else:
        same_xs = run.run("g6_cross_section_same", cand, override_spec(
            [{"kind": "cross_section", "nuclide": "Fe56", "mt": 103, "value": inspect_value}]))
        sub["cross_section_same"] = ({"pass": False, "error": "run failed; see runs"} if same_xs is None else
                                     {**compare_results(base, same_xs, ALLOWED_OVERRIDES),
                                      "ledger_overrides": same_xs["ledger"].get("overrides")})
        if same_xs is not None:
            sub["cross_section_same"]["pass"] = sub["cross_section_same"]["identical_apart_from_echoes"]
    scaled = run.run("g6_cross_section_x2", cand, override_spec(
        [{"kind": "cross_section_scale", "nuclide": "Fe56", "mt": 103, "value": 2.0}]))
    if scaled is None:
        sub["cross_section_scale_x2"] = {"pass": False, "error": "run failed; see runs"}
    else:
        n_c, a_c = mn56_of(scaled)
        checks = Checks()
        if any(v is None for v in n_c):
            checks.equal("Mn56 present at every step", "", False, True)
        else:
            predicted = mn56_series(lam, production + rate_np, steps)
            for i, (n, a, p) in enumerate(zip(n_c, a_c, predicted)):
                checks.close("Mn56 atoms", f"step{i + 1}", n, p, OVERRIDE_TOL)
                checks.close("Mn56 activity", f"step{i + 1}", a, lam * p, OVERRIDE_TOL)
            checks.close("Mn56 production increment (end of step 1)", "",
                         (n_c[0] - n_base[0]) * lam / (m0 * -math.expm1(-lam * dt0)), rate_np, OVERRIDE_TOL)
        entry = (scaled["ledger"].get("overrides") or [{}])[0]
        before, after = entry.get("base_spectrum_collapsed_barns_before"), entry.get(
            "base_spectrum_collapsed_barns_after")
        sub["cross_section_scale_x2"] = {
            "checks": checks.report(), "ledger_overrides": scaled["ledger"].get("overrides"),
            "ledger_before_vs_inspect": None if before is None or inspect_value is None
            else rel_diff(before, inspect_value),
            "ledger_after_over_before": None if not before else after / before,
            "pass": checks.n_bad == 0}
        # report-only: an override equal to the collapsed value the run itself reports
        if before is not None:
            twin = run.run("g6_cross_section_same_ledger_value", cand, override_spec(
                [{"kind": "cross_section", "nuclide": "Fe56", "mt": 103, "value": before}]))
            sub["cross_section_same_ledger_value_report_only"] = (
                {"error": "run failed; see runs"} if twin is None else
                compare_results(base, twin, ALLOWED_OVERRIDES))
    out["sub"] = sub
    out["pass"] = all(v.get("pass") for k, v in sub.items() if "report_only" not in k)
    return out


def gate_g7(ctx):
    run, cand, base_spec, base = ctx["run"], ctx["candidate"], ctx["base_spec"], ctx["base_result"]
    if base is None:
        return {"pass": False, "error": "the baseline FNS run failed"}
    composition = {row["nuclide"]: row["atoms_per_g"] for row in base["steps"][0]["inventory"]}
    cooling = copy.deepcopy(base_spec["schedule"][1:])
    material = {"mass_g": base_spec["material"]["mass_g"], "basis": "atoms_per_g", "composition": composition}
    options = dict(base_spec.get("options") or {})
    options["mode"] = "coupled"
    decay_only = {"spec": "actinv-spec-1", "title": base_spec["title"], "projectile": "neutron",
                  "decay": base_spec["decay"], "material": material, "schedule": cooling, "options": options}
    full = copy.deepcopy(decay_only)
    full["library"] = base_spec["library"]
    full["spectrum"] = base_spec["spectrum"]
    out = {"initial_nuclides": len(composition), "cooling_steps": len(cooling),
           "inventory_source": "steps[0].inventory of the candidate FNS run (mode %s)" % base.get("mode")}
    res_d = run.run("g7_decay_only", cand, decay_only)
    res_f = run.run("g7_full", cand, full)
    if res_d is None or res_f is None:
        out.update({"pass": False, "error": "a run failed; see runs"})
        return out
    checks = Checks()
    checks.equal("ledger.decay_only", "", res_d["ledger"].get("decay_only"), True)
    checks.equal("certificate.decay_only", "", res_d["certificate"].get("decay_only"), True)
    checks.equal("certificate.inputs.library is null", "", res_d["certificate"]["inputs"].get("library"), None)
    checks.equal("full run is not decay-only", "", res_f["ledger"].get("decay_only"), None)
    checks.equal("same step count", "", len(res_d["steps"]), len(res_f["steps"]))
    for i, (sd, sf) in enumerate(zip(res_d["steps"], res_f["steps"])):
        inv_d, inv_f = inventory(sd), inventory(sf)
        total = max(math.fsum(inv_d.values()), math.fsum(inv_f.values()))
        for name in sorted(set(inv_d) | set(inv_f)):
            a, b = inv_d.get(name, 0.0), inv_f.get(name, 0.0)
            if max(a, b) <= SMALL_FRACTION * total:
                continue
            checks.close("inventory_atoms_per_g", f"step{i + 1}/{name}", a, b, DECAY_ONLY_TOL)
        checks.close("total_atoms", f"step{i + 1}", math.fsum(inv_d.values()), math.fsum(inv_f.values()),
                     DECAY_ONLY_TOL)
    out.update({"checks": checks.report(), "mode_decay_only": res_d.get("mode"), "mode_full": res_f.get("mode"),
                "pass": checks.n_bad == 0})
    return out


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="release binary built from the P124 base commit 2f3daa4")
    ap.add_argument("--candidate", required=True, help="release binary of the integrated P124 implementation")
    ap.add_argument("--workdir", default="target/p124-work")
    ap.add_argument("--out", default="results/p124_inputs.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per run")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in the workdir")
    args = ap.parse_args(argv)
    # Runs execute from the repository root, so the binaries are made absolute.
    reference = Path(args.reference).resolve()
    candidate = Path(args.candidate).resolve()
    workdir = Path(args.workdir)
    workdir = workdir if workdir.is_absolute() else ROOT / workdir
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path

    base_spec = json.loads(GATE_SPEC.read_text())
    library_ref = base_spec["library"]["path"]
    library = (ROOT / library_ref).resolve()
    decay_primary = (ROOT / base_spec["decay"]["primary"]).resolve()
    decay_fallback = (ROOT / base_spec["decay"]["fallback"]).resolve() if base_spec["decay"].get("fallback") else None
    index_path = Path(str(library)[:-4] + "_index.json")
    needed = {"reference binary": reference, "candidate binary": candidate, "library": library,
              "library index": index_path, "decay primary": decay_primary, "tables.rs": TABLES_RS,
              "709 groups": GROUPS_709, "175 groups": GROUPS_175, "protocol": PROTOCOL}
    if decay_fallback:
        needed["decay fallback"] = decay_fallback
    missing = [f"{label}: {path}" for label, path in needed.items() if not Path(path).is_file()]
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2
    workdir.mkdir(parents=True, exist_ok=True)

    record = {"protocol": "ACTINV-P124", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL),
              "reference_binary": record_file(reference), "candidate_binary": record_file(candidate),
              "inputs": {label: record_file(path) for label, path in needed.items()
                         if label not in ("reference binary", "candidate binary", "protocol")},
              "gate_spec": record_file(GATE_SPEC), "tolerances": {
                  "conservation": CONSERVATION_TOL, "conversion": CONVERSION_TOL, "rebin_proxy": PROXY_REL_TOL,
                  "partition": PARTITION_TOL, "overrides": OVERRIDE_TOL, "decay_only": DECAY_ONLY_TOL,
                  "small_fraction_of_total": SMALL_FRACTION},
              "runs": {}, "gates": {"G1": {"external": "fmt, clippy -D warnings and workspace tests are not run here"},
                                    "G8": {"external": "CI on the pushed commit is not run here"}}}
    run = Runner(workdir, args.timeout, args.reuse, record)
    ctx = {"run": run, "candidate": candidate, "base_spec": base_spec, "workdir": workdir, "record": record,
           "library": library, "library_ref": library_ref, "library_groups": 709, "spec_path": str(GATE_SPEC),
           "decay_primary": decay_primary, "decay_fallback": decay_fallback}
    ctx["ref_result"] = run.run("g2_reference", reference, GATE_SPEC)
    ctx["base_result"] = run.run("g2_candidate", candidate, GATE_SPEC)

    for name, fn in (("G2", gate_g2), ("G3", gate_g3), ("G4", gate_g4), ("G5", gate_g5), ("G6", gate_g6),
                     ("G7", gate_g7)):
        try:
            record["gates"][name] = fn(ctx)
        except (OSError, ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, zipfile.BadZipFile) as error:
            record["gates"][name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    overall = all(bool(record["gates"][g].get("pass")) for g in ("G2", "G3", "G4", "G5", "G6", "G7"))
    record["verdict"] = "P124-PASS" if overall else "P124-FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for gate in ("G2", "G3", "G4", "G5", "G6", "G7"):
        g = record["gates"][gate]
        extra = f" ({g['error']})" if "error" in g else ""
        print(f"P124 {gate}: {'PASS' if g.get('pass') else 'FAIL'}{extra}")
    print(f"P124 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
