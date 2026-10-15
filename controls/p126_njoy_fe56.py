#!/usr/bin/env python3
"""P126 G3 helper (protocols/ACTINV-P126_PROTOCOL.md): a kerma table for Fe-56 from NJOY2016, and the comparison of
the table with the GENDF it was built from.

Steps, serial, no shell, each under a timeout:
  1. Write an NJOY2016 input deck: MODER (ASCII to binary), RECONR (0.1 percent), BROADR to 293.6 K, HEATR with
     `local = 0` (photons transported, not deposited at the reaction site; HEATR deposits locally where an evaluation
     lacks photon files) and the total damage energy MT=444, GROUPR on the FISPACT-II 709-group structure
     (crates/actinv-data/data/fispact_709_groups.json, reversed to ascending) with iwt = 3, the 1/E weighting, one
     temperature (293.6 K), one sigma0 (1e10, infinite dilution) and Legendre order 0, for MF=3 MT=301 (total heating
     kerma) and MT=444 (total damage energy production).
  2. Run the njoy executable on ~/nuclear-data/tendl-2025/files/n/n-Fe056.tendl in the work directory.
  3. Run scripts/build_kerma_table.py on the GENDF (and, as a report-only comparison, on the HEATR PENDF collapsed
     with its own flat-in-lethargy weight onto the same boundaries).
  4. Parse the GENDF MF=1/MT=451 group boundaries and MF=3/MT=301 (and MT=444) group values with this script's own
     ENDF-6 reader, and compare them with the table. PASS iff the table's Fe56 MT=301 values equal the GENDF values
     within 1e-12 relative (exactly zero where the GENDF is zero) in all 709 groups, the table carries exactly the
     tape's boundaries, and those boundaries equal the vendored 709-group structure and the activation library's.

The weighting is stated in the summary: GROUPR iwt = 3 (1/E, i.e. flat in lethargy) with infinite dilution. The
`local = 0` photon choice is recorded in the table's `source.edition`, as docs/guide/specification.md asks.

Usage:
  p126_njoy_fe56.py --workdir DIR [--out DIR/njoy_fe56.json] [--njoy ~/nuclear-data/njoy2016.79-build/njoy]
      [--endf ~/nuclear-data/tendl-2025/files/n/n-Fe056.tendl] [--library LIB.npz] [--timeout 3600] [--reuse]
      [--local 0|1]

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 when NJOY itself could not be run.
"""
import argparse
import array
import ast
import hashlib
import json
import math
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_kerma_table.py"
GROUPS_JSON = ROOT / "crates" / "actinv-data" / "data" / "fispact_709_groups.json"
DEFAULT_NJOY = "~/nuclear-data/njoy2016.79-build/njoy"
DEFAULT_ENDF = "~/nuclear-data/tendl-2025/files/n/n-Fe056.tendl"
DEFAULT_LIBRARY = ROOT / "actinv-data" / "v1.1.0" / "activation" / "tendl-2025-neutron-709g.npz"
TEMPERATURE_K = 293.6
RECONR_TOLERANCE = 0.001
BROADR_TOLERANCE = 0.001
SIGMA0 = 1.0e10
IWT = 3  # GROUPR weight function 3: 1/E
TOL = 1e-12
FE56_ZA = 26056
TARGET = "Fe56"
MAX_LISTED = 20


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


# ------------------------------------------------------------------------------ ENDF-6 reading (own implementation)


def endf_number(field):
    """ENDF-6 real: blank is zero; `1.234567+3` means 1.234567e3; plain floats are accepted too."""
    text = field.strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    for i in range(len(text) - 1, 0, -1):
        if text[i] in "+-" and text[i - 1] not in "eE":
            return float(text[:i] + "e" + text[i:])
    raise ValueError(f"unparseable ENDF number {field!r}")


def endf_integer(field):
    text = field.strip()
    return int(float(text)) if text else 0


def sections(path):
    """Yield (mat, mf, mt, lines) for each MAT/MF/MT section of a tape; `lines` are the 66 data columns."""
    key, lines = None, []
    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            mat, mf, mt = line[66:70].strip(), line[70:72].strip(), line[72:75].strip()
            if not (mat.lstrip("-").isdigit() and mf.isdigit() and mt.isdigit()):
                continue
            tag = (int(mat), int(mf), int(mt))
            if tag[0] <= 0 or tag[1] == 0 or tag[2] == 0:
                if key is not None:
                    yield key + (lines,)
                    key, lines = None, []
                continue
            if tag != key:
                if key is not None:
                    yield key + (lines,)
                key, lines = tag, []
            lines.append(line[:66].ljust(66))
    if key is not None:
        yield key + (lines,)


def record(lines, index):
    """The six fields of the CONT-like record on line `index`: (C1, C2, L1, L2, N1, N2)."""
    f = [lines[index][11 * k:11 * (k + 1)] for k in range(6)]
    return (endf_number(f[0]), endf_number(f[1]), endf_integer(f[2]), endf_integer(f[3]), endf_integer(f[4]),
            endf_integer(f[5]))


def data_words(lines, index, count):
    """`count` 11-character words from consecutive lines starting at `index`; returns (words, next line)."""
    words = []
    while len(words) < count:
        line = lines[index]
        take = min(6, count - len(words))
        words.extend(line[11 * k:11 * (k + 1)] for k in range(take))
        index += 1
    return words, index


def endf_mat_and_za(path):
    """MAT and ZA of the first material on an ENDF-6 tape (its MF=1/MT=451 HEAD)."""
    for mat, mf, mt, lines in sections(path):
        if (mf, mt) == (1, 451):
            za, awr, _l1, _l2, _n1, _n2 = record(lines, 0)
            return mat, int(round(za)), awr
    raise ValueError(f"{path}: no MF=1/MT=451 section")


def parse_gendf(path):
    """Group boundaries (EGN) and the MF=3 group values by MT of the first material of a GENDF tape.

    MF=1/MT=451: HEAD (ZA, AWR, 0, NZ, -1, NTW), then LIST (TEMP, 0, NGN, NGG, NW, 0) with the words TITLE (NTW),
    SIGZ (NZ), EGN (NGN + 1), EGG (NGG + 1). MF=3: HEAD (ZA, AWR, NL, NZ, LRFLAG, NGN), then per group a LIST
    (TEMP, 0, NG2, IG2LO, NW, IG) whose NW words are (flux, cross section) for a vector reaction with NL = NZ = 1.
    Groups the tape does not write are zero."""
    egn, groups, temperature, mat_seen = None, {}, None, None
    for mat, mf, mt, lines in sections(path):
        if mat_seen is None:
            mat_seen = mat
        if mat != mat_seen:
            continue  # one material per tape here
        if (mf, mt) == (1, 451):
            _za, _awr, _l1, nz, _n1, ntw = record(lines, 0)
            temperature, _c2, ngn, ngg, nw, _n2 = record(lines, 1)
            words, _ = data_words(lines, 2, nw)
            if nz != 1:
                raise ValueError(f"GENDF carries {nz} sigma0 values; expected one")
            start = ntw + nz
            egn = [endf_number(w) for w in words[start:start + ngn + 1]]
        elif mf == 3 and mt in (301, 444):
            if egn is None:
                raise ValueError("MF=3 before MF=1/MT=451")
            ngn = len(egn) - 1
            _za, _awr, nl, nz, _lrflag, _ngi = record(lines, 0)
            if (nl, nz) != (1, 1):
                raise ValueError(f"MF=3/MT={mt} has NL={nl}, NZ={nz}")
            values = [0.0] * ngn
            seen, index = set(), 1
            while index < len(lines):
                _temp, _c2, ng2, _ig2lo, nw, group = record(lines, index)
                words, index = data_words(lines, index + 1, nw)
                if ng2 != 2 or nw != 2 or not 1 <= group <= ngn or group in seen:
                    raise ValueError(f"MF=3/MT={mt}: bad group record NG2={ng2} NW={nw} IG={group}")
                seen.add(group)
                values[group - 1] = endf_number(words[1])
            groups[mt] = values
    if egn is None:
        raise ValueError(f"{path}: no MF=1/MT=451 section")
    return {"boundaries": egn, "values": groups, "temperature_K": temperature, "mat": mat_seen}


# ------------------------------------------------------------------------------ group structures


def vendored_boundaries():
    data = json.loads(GROUPS_JSON.read_text())
    boundaries = [float(b) for b in data["boundaries_eV"]]
    return sorted(boundaries)  # published descending; GROUPR and the library want ascending


def library_boundaries(npz_path):
    with zipfile.ZipFile(npz_path) as archive, archive.open("bounds.npy") as handle:
        if handle.read(6) != b"\x93NUMPY":
            raise ValueError("bounds.npy is not an npy member")
        major, _minor = handle.read(2)
        (length,) = struct.unpack("<H" if major == 1 else "<I", handle.read(2 if major == 1 else 4))
        header = ast.literal_eval(handle.read(length).decode("latin1").strip())
        values = array.array("d")
        values.frombytes(handle.read(8 * header["shape"][0]))
    return list(values)


# ------------------------------------------------------------------------------ NJOY deck


def njoy_deck(mat, boundaries, local):
    lines = [
        "moder",
        " 20 -21/",
        "reconr",
        " -21 -22/",
        " 'TENDL-2025 Fe-56 pointwise for ACTINV P126'/",
        f" {mat} 1 0/",
        f" {RECONR_TOLERANCE}/",
        " 'processed for the ACTINV kerma table check'/",
        " 0/",
        "broadr",
        " -21 -22 -23/",
        f" {mat} 1/",
        f" {BROADR_TOLERANCE}/",
        f" {TEMPERATURE_K}/",
        " 0/",
        "heatr",
        " -21 -23 -24/",
        f" {mat} 1 0 0 {local} 0/",
        " 444/",
        "moder",
        " -24 25/",
        "groupr",
        " -21 -24 0 -26/",
        f" {mat} 1 0 {IWT} 0 1 1 1/",
        " 'Fe-56 TENDL-2025 kerma, FISPACT-II 709 groups, 1/E weight'/",
        f" {TEMPERATURE_K}/",
        f" {SIGMA0:.1e}/",
        f" {len(boundaries) - 1}/",
    ]
    for i in range(0, len(boundaries), 4):
        lines.append(" " + " ".join(repr(b) for b in boundaries[i:i + 4]))
    lines[-1] += "/"
    lines += [
        " 3 301 'total heating kerma'/",
        " 3 444 'total damage energy production'/",
        " 0/",
        " 0/",
        "moder",
        " -26 27/",
        "stop",
    ]
    return "\n".join(lines) + "\n"


def run(command, cwd, timeout, stdin_path=None, stdout_path=None, stderr_path=None):
    """One subprocess, no shell. Returns (exit code or None, error text or None)."""
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    stderr = open(stderr_path, "wb") if stderr_path else subprocess.STDOUT
    try:
        proc = subprocess.run([str(c) for c in command], cwd=str(cwd), stdin=stdin, stdout=stdout, stderr=stderr,
                              timeout=timeout)
        return proc.returncode, None
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout} s"
    except OSError as error:
        return None, f"{type(error).__name__}: {error}"
    finally:
        for handle in (stdin, stdout, stderr):
            if hasattr(handle, "close"):
                handle.close()


def tail(path, count=25):
    try:
        return Path(path).read_text(errors="replace").splitlines()[-count:]
    except OSError:
        return []


# ------------------------------------------------------------------------------ comparison


def compare_rows(table_row, gendf_row):
    worst, bad, n_nonzero = 0.0, [], 0
    for group, (got, expected) in enumerate(zip(table_row, gendf_row), start=1):
        if expected == 0.0:
            err = 0.0 if got == 0.0 else math.inf
        else:
            n_nonzero += 1
            err = abs(got - expected) / abs(expected)
        worst = max(worst, err)
        if err > TOL and len(bad) < MAX_LISTED:
            bad.append({"group": group, "table": got, "gendf": expected})
    return {"groups": len(gendf_row), "nonzero_groups": n_nonzero, "worst_relative_error": worst,
            "n_bad": sum(1 for g, e in zip(table_row, gendf_row)
                         if (e == 0.0 and g != 0.0) or (e != 0.0 and abs(g - e) / abs(e) > TOL)),
            "mismatches": bad}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", help="summary JSON (default WORKDIR/njoy_fe56.json)")
    ap.add_argument("--njoy", default=DEFAULT_NJOY)
    ap.add_argument("--endf", default=DEFAULT_ENDF)
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY), help="activation library whose bounds must match")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per subprocess")
    ap.add_argument("--local", type=int, choices=(0, 1), default=0, help="HEATR: 1 deposits photon energy locally")
    ap.add_argument("--reuse", action="store_true", help="reuse tape25/tape27 already in the work directory")
    args = ap.parse_args(argv)

    workdir = Path(args.workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out).expanduser().resolve() if args.out else workdir / "njoy_fe56.json"
    njoy = Path(args.njoy).expanduser().resolve()
    endf = Path(args.endf).expanduser().resolve()
    summary = {"protocol": "ACTINV-P126", "gate": "G3", "script": record_file(Path(__file__).resolve()),
               "builder": record_file(BUILDER), "weighting": {"groupr_iwt": IWT, "description": "1/E (flat in lethargy)",
                                                                "sigma0": SIGMA0, "legendre_order": 0},
               "temperature_K": TEMPERATURE_K, "reconr_tolerance": RECONR_TOLERANCE, "heatr_local": args.local,
               "tolerance": TOL, "pass": False}

    def finish(code):
        out_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
        print(f"P126 G3 (NJOY Fe-56 kerma): {'PASS' if summary['pass'] else 'FAIL'} ({out_path})")
        return code

    if not njoy.is_file():
        summary["error"] = f"njoy executable not found: {njoy}"
        return finish(2)
    if not endf.is_file():
        summary["error"] = f"ENDF file not found: {endf}"
        return finish(2)
    summary["njoy"] = record_file(njoy)
    summary["endf"] = record_file(endf)

    mat, za, awr = endf_mat_and_za(endf)
    summary["evaluation"] = {"mat": mat, "za": za, "awr": awr}
    if za != FE56_ZA:
        summary["error"] = f"the evaluation is ZA {za}, expected {FE56_ZA}"
        return finish(2)

    boundaries = vendored_boundaries()
    summary["groups"] = {"vendored": record_file(GROUPS_JSON), "n_groups": len(boundaries) - 1}
    try:
        lib_bounds = library_boundaries(args.library)
        summary["groups"]["library"] = record_file(args.library)
        summary["groups"]["vendored_equals_library_exactly"] = lib_bounds == boundaries
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        lib_bounds = None
        summary["groups"]["library_error"] = f"{type(error).__name__}: {error}"

    deck = njoy_deck(mat, boundaries, args.local)
    (workdir / "input").write_text(deck)
    summary["deck"] = record_file(workdir / "input")
    tape20 = workdir / "tape20"
    if tape20.exists() or tape20.is_symlink():
        tape20.unlink()
    tape20.write_bytes(endf.read_bytes())
    pendf, gendf = workdir / "tape25", workdir / "tape27"
    if not (args.reuse and pendf.exists() and gendf.exists()):
        for stale in (pendf, gendf):
            if stale.exists():
                stale.unlink()
        code, error = run([njoy], workdir, args.timeout, stdin_path=workdir / "input",
                          stdout_path=workdir / "output", stderr_path=workdir / "njoy.stderr")
        summary["njoy_run"] = {"exit_code": code, "error": error}
        if code != 0 or not pendf.exists() or not gendf.exists():
            summary["error"] = error or f"njoy exit code {code}; tapes written: {pendf.exists()} {gendf.exists()}"
            summary["njoy_output_tail"] = tail(workdir / "output")
            summary["njoy_stderr_tail"] = tail(workdir / "njoy.stderr")
            return finish(2)
    summary["tapes"] = {"pendf": record_file(pendf), "gendf": record_file(gendf), "output": record_file(workdir / "output")}

    # the table through the converter (GENDF), and the report-only PENDF collapse
    table_path = workdir / "fe56_kerma_gendf.json"
    edition = (f"TENDL-2025 n-Fe056; NJOY2016.79 RECONR {RECONR_TOLERANCE}, BROADR {TEMPERATURE_K} K, HEATR local={args.local} "
               f"({'photon energy deposited locally' if args.local else 'photons transported'}), GROUPR iwt={IWT} (1/E) "
               f"sigma0={SIGMA0:g}")
    base = [sys.executable, "-I", BUILDER]
    code, error = run(base + [table_path, "--gendf", gendf, "--group-structure", "fispact-709", "--temperature-K",
                              TEMPERATURE_K, "--damage-energy", "--edition", edition, "--url", str(endf)],
                      ROOT, args.timeout, stdout_path=workdir / "build_gendf.log")
    summary["builder_run"] = {"exit_code": code, "error": error, "log_tail": tail(workdir / "build_gendf.log", 5)}
    if code != 0:
        summary["error"] = error or f"build_kerma_table.py exit code {code}"
        return finish(1)
    summary["table"] = record_file(table_path)

    parsed = parse_gendf(gendf)
    table = json.loads(table_path.read_text())
    summary["gendf"] = {"mat": parsed["mat"], "temperature_K": parsed["temperature_K"],
                        "mts_parsed": sorted(parsed["values"]), "n_groups": len(parsed["boundaries"]) - 1}
    checks = {}
    checks["table_boundaries_equal_tape_egn"] = table["boundaries_eV"] == parsed["boundaries"]
    checks["tape_egn_equals_vendored_exactly"] = parsed["boundaries"] == boundaries
    if lib_bounds is not None:
        checks["tape_egn_equals_library_exactly"] = parsed["boundaries"] == lib_bounds
        checks["max_relative_boundary_difference_to_library"] = max(
            abs(a - b) / b for a, b in zip(parsed["boundaries"], lib_bounds)) if len(lib_bounds) == len(
                parsed["boundaries"]) else None
    checks["table_targets"] = sorted(table["targets"])
    if TARGET not in table["targets"] or 301 not in parsed["values"]:
        summary["checks"] = checks
        summary["error"] = f"the table has no {TARGET} MT=301 row or the GENDF has no MT=301"
        return finish(1)
    kerma = compare_rows(table["targets"][TARGET], parsed["values"][301])
    checks["mt301"] = kerma
    if 444 in parsed["values"] and TARGET in table.get("damage_energy_targets", {}):
        checks["mt444_report_only"] = compare_rows(table["damage_energy_targets"][TARGET], parsed["values"][444])
    checks["negative_mt301_groups"] = sum(1 for v in parsed["values"][301] if v < 0.0)
    checks["mt301_nonzero_groups"] = sum(1 for v in parsed["values"][301] if v != 0.0)
    checks["mt301_total_ev_barn_groups_sum"] = sum(parsed["values"][301])
    summary["checks"] = checks

    # report-only: the HEATR PENDF collapsed by the converter's own flat-in-lethargy weight
    bounds_json = workdir / "boundaries_ascending.json"
    bounds_json.write_text(json.dumps({"boundaries_eV": boundaries}) + "\n")
    pendf_table = workdir / "fe56_kerma_pendf.json"
    code, error = run(base + [pendf_table, "--pendf", pendf, "--boundaries-from", bounds_json, "--group-structure",
                              "fispact-709", "--temperature-K", TEMPERATURE_K, "--damage-energy", "--edition",
                              edition + " (PENDF collapse)", "--url", str(endf)],
                      ROOT, args.timeout, stdout_path=workdir / "build_pendf.log")
    report = {"exit_code": code, "error": error}
    if code == 0:
        other = json.loads(pendf_table.read_text())
        report["table"] = record_file(pendf_table)
        row_a, row_b = table["targets"][TARGET], other["targets"].get(TARGET)
        if row_b is not None:
            diffs = [abs(a - b) / abs(a) for a, b in zip(row_a, row_b) if a != 0.0]
            report["groupr_vs_pendf_collapse"] = {
                "groups_compared": len(diffs), "median_relative_difference": sorted(diffs)[len(diffs) // 2] if diffs else None,
                "max_relative_difference": max(diffs) if diffs else None,
                "note": "GROUPR integrates the kerma against 1/E on its own union grid; the converter collapses the "
                        "PENDF's linear-linear points with the same weight. Report only."}
    summary["pendf_collapse_report_only"] = report

    gates = {"mt301_equal_within_tolerance": kerma["n_bad"] == 0, "table_boundaries_equal_tape": checks[
        "table_boundaries_equal_tape_egn"], "tape_boundaries_equal_vendored": checks["tape_egn_equals_vendored_exactly"],
             "mt301_has_nonzero_groups": checks["mt301_nonzero_groups"] > 0}
    if lib_bounds is not None:
        gates["tape_boundaries_equal_library"] = checks["tape_egn_equals_library_exactly"]
    summary["criteria"] = gates
    summary["pass"] = all(gates.values())
    return finish(0 if summary["pass"] else 1)


if __name__ == "__main__":
    sys.exit(main())
