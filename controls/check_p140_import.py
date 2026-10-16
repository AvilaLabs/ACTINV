#!/usr/bin/env python3
"""P140 gate H3 (protocols/ACTINV-P140_PROTOCOL.md): importer breadth of `actinv import-fispact`.

Inputs: the 132 CoNDERC FNS decks, the 2 CoNDERC fission decks, the 6 decks of UKAEA's `pypact` (Apache-2.0, commit
e28091c1cb41fbd48019284643b8fdc29f687988: the five reference/*.i decks and examples/files/test.i), and synthetic decks
generated here (never executed or imported: decks are read as text only):

  keyword_valid        one minimal valid deck per known keyword, in the keyword's own phase
  keyword_malformed    the same keyword with malformed arguments (missing, extra, non-numeric, negative, 1e308, 1e309,
                       Fortran exponents 1.0D+3 / 1.0+3, empty, ...)
  keyword_wrong_phase  every keyword in each of the three phases it does not belong to (library, initial, inventory)
  keyword_abbrev       abbreviations, prefixes, lower and mixed case, and the ambiguous / too-short prefixes
  combo                realistic combinations (POWER+DENSITY, nested PULSE with ZERO, TIME units, OVER, PATH/ROUTES
                       with UNCERTAINTY, GRPCONVERT, projectiles, fuel, a deck carrying every keyword)
  lexical              comments spanning lines, tabs, CRLF, trailing garbage, non-ASCII, invalid UTF-8, empty decks
  cli                  command-line and files-file edge cases
  stress               very large or very deep decks

Per deck the candidate is run as `CAND import-fispact DECK OUT.json [--fluxes F | --files FILES]` from the repository
root, without a shell, under a timeout. Pass:
  - no crash or panic: no exit code 101 or signal, no 'panicked at' / 'RUST_BACKTRACE' / 'stack backtrace' / stack
    overflow text, no timeout;
  - the exit code is 0, or 1 or 2 with a message on stderr;
  - on exit 0 the sidecar OUT.import.json is well formed (schema, entries, counts) and every keyword occurrence in the
    deck has an entry with the right phase and one status of mapped, approximated, noted, unsupported. The occurrences
    come from this script's own port of the importer's lexer and parser (the rules in fispact_import.rs): `*` text
    lines, unknown words, keywords by their leading characters, MASS / FUEL / TIME / PULSE structure. Not expected to
    have an entry, by the importer's design: ENDPULSE (closes its PULSE entry), lines after END, and keywords in a PULSE
    with a repeat count of 0 (listed under `unreported_by_design`, not failed). A parse error in the port that the
    importer does not share is a failure;
  - on exit 0 the same deck with `--strict` exits non-zero iff the sidecar has an unsupported entry (and then names
    --strict on stderr and writes no spec); otherwise it exits 0 with the same spec;
  - real decks (FNS, fission, pypact): the spec passes `CAND validate OUT.json`; FNS specs also pass
    `validate --files` (as in P132). For the fission and pypact decks `validate --files` is recorded only (their data
    paths need not resolve here). A pypact deck without a files file is imported as it is and again with a synthetic
    fluxes file; only the second spec must validate (the first is recorded).

One line per deck group, then the verdict line. Overall PASS iff every deck passes and the corpus sizes are 132, 2 and 6.

Usage:
  check_p140_import.py --candidate BIN [--workdir target/p140-import] [--out results/p140_import.json]
      [--timeout 300] [--fns ~/nuclear-data/conderc-fns/fns]
      [--fission ~/nuclear-data/conderc-fission/fission/Fispact-II/Inputs]
      [--pypact ~/nuclear-data/ext-decks/pypact] [--keep-all]
  check_p140_import.py --self-check [--workdir target/p140-import]
      generates every synthetic deck, lists the counts per category and cross-checks the keyword table against
      crates/actinv-cli/src/fispact_import.rs; runs nothing.

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on a missing input.
"""
import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "protocols" / "ACTINV-P140_PROTOCOL.md"
IMPORT_RS = ROOT / "crates" / "actinv-cli" / "src" / "fispact_import.rs"
DEFAULT_FNS = Path("~/nuclear-data/conderc-fns/fns").expanduser()
DEFAULT_FISSION = Path("~/nuclear-data/conderc-fission/fission/Fispact-II/Inputs").expanduser()
DEFAULT_PYPACT = Path("~/nuclear-data/ext-decks/pypact").expanduser()
PYPACT_COMMIT = "e28091c1cb41fbd48019284643b8fdc29f687988"
PYPACT_LICENSE = "Apache-2.0"
EXPECTED = {"fns": 132, "fission": 2, "pypact": 6}
STATUSES = ("mapped", "approximated", "noted", "unsupported")
PHASES = ("library", "initial", "inventory")
REPORT_SCHEMA = "actinv-fispact-import-report-1"
PANIC_MARKERS = ("panicked at", "RUST_BACKTRACE", "stack backtrace", "overflowed its stack", "fatal runtime error",
                 "Segmentation fault", "memory allocation of")
ALLOWED_ERROR_CODES = (1, 2)
STRESS_TIMEOUT = 60
MAX_LISTED = 40
CATEGORIES = ("keyword_valid", "keyword_malformed", "keyword_wrong_phase", "keyword_abbrev", "combo", "lexical",
              "cli", "stress")

# ------------------------------------------------------------------------------ the importer's keyword table
# (name, arity, own phase, plausible arguments). The arities are those of KEYWORDS in fispact_import.rs
# (--self-check compares the names and arities with the source); the phase is where a deck normally has the keyword.

KEYWORD_TABLE = [
    ("ALLDISPEN", "Numbers", "initial", "1"),
    ("ATDISPEN", "Words", "initial", "FE56 40.0"),
    ("ATOMS", "Zero", "inventory", ""),
    ("ATWO", "Zero", "initial", ""),
    ("BREMSSTRAHLUNG", "Words", "initial", "2 SR90 Y90"),
    ("CLEAR", "Zero", "initial", ""),
    ("CLOBBER", "Zero", "library", ""),
    ("CNVTYPE", "Numbers", "library", "0"),
    ("COMPRESSXS", "Numbers", "library", "1"),
    ("COVARIANCE", "Zero", "library", ""),
    ("CULTAB", "Zero", "initial", ""),
    ("CUMFYLD", "Zero", "initial", ""),
    ("DENSITY", "Numbers", "initial", "7.875"),
    ("DEPLETION", "Words", "initial", "1"),
    ("DOSE", "Numbers", "initial", "2 1.0"),
    ("END", "Special", "inventory", ""),
    ("ENDPULSE", "Zero", "inventory", ""),
    ("ERROR", "Words", "initial", "1"),
    ("EXTRACTXS", "Words", "library", "1"),
    ("FISCHOOSE", "Words", "initial", "1 U235"),
    ("FISPACT", "Special", "initial", ""),
    ("FISYIELD", "Words", "initial", "1 U235"),
    ("FLUX", "Numbers", "inventory", "2.0E+10"),
    ("FUEL", "Special", "initial", "1"),
    ("FULLXS", "Zero", "library", ""),
    ("GENERIC", "Numbers", "initial", "1"),
    ("GETDECAY", "Numbers", "library", "0"),
    ("GETXS", "Numbers", "library", "0"),
    ("GRAPH", "Numbers", "initial", "5 2 1 1 2 3 4 5"),
    ("GROUP", "Numbers", "initial", "1"),
    ("GRPCONVERT", "Numbers", "library", "709 709"),
    ("HALF", "Zero", "initial", ""),
    ("HAZARDS", "Zero", "initial", ""),
    ("INDEXPATH", "Zero", "library", ""),
    ("IRON", "Zero", "initial", ""),
    ("JSON", "Zero", "library", ""),
    ("LIBVERSION", "Numbers", "library", "1"),
    ("LIMGRP", "Numbers", "library", "1.0E+5 1.4E+7"),
    ("LOGLEVEL", "Numbers", "library", "1"),
    ("LOOKAHEAD", "Zero", "initial", ""),
    ("MASS", "Special", "initial", "1.0E-3 1"),
    ("MCSAMPLE", "Numbers", "initial", "10"),
    ("MIND", "Numbers", "initial", "1.E3"),
    ("MONITOR", "Numbers", "library", "1"),
    ("NOCOMP", "Zero", "initial", ""),
    ("NOERROR", "Zero", "initial", ""),
    ("NOFISS", "Zero", "initial", ""),
    ("NOSORT", "Zero", "initial", ""),
    ("NOSTABLE", "Zero", "initial", ""),
    ("NOT1", "Zero", "initial", ""),
    ("NOT2", "Zero", "initial", ""),
    ("NOT3", "Zero", "initial", ""),
    ("NOT4", "Zero", "initial", ""),
    ("NOXRAY", "Zero", "initial", ""),
    ("NUCGRAPH", "Numbers", "initial", "1 0.01 1 2"),
    ("OVER", "Words", "initial", "FE56 ACROSS FE57 1.0 ALAM 100 DAYS"),
    ("PARTITION", "Words", "inventory", "1 FE 0.5"),
    ("PATH", "Words", "initial", "FE55"),
    ("PATHRESET", "Numbers", "initial", "1"),
    ("POWER", "Words", "inventory", "1.0E-3 1 301"),
    ("PRINTLIB", "Numbers", "library", "1"),
    ("PROBTABLE", "Numbers", "library", "1"),
    ("PROJECTILE", "Numbers", "library", "1"),
    ("PULSE", "Special", "inventory", "2"),
    ("READGG", "Zero", "library", ""),
    ("READSF", "Zero", "library", ""),
    ("RESULT", "Words", "initial", "1"),
    ("ROUTES", "Words", "initial", "MN54"),
    ("SAVELINES", "Zero", "library", ""),
    ("SENSITIVITY", "Words", "initial", "1"),
    ("SORTDOMINANT", "Numbers", "initial", "100 20"),
    ("SPECTRUM", "Zero", "inventory", ""),
    ("SPEK", "Zero", "library", ""),
    ("SPLIT", "Numbers", "initial", "1"),
    ("SSFCHOOSE", "Words", "library", "1 FE56"),
    ("SSFDILUTION", "Words", "library", "1.0"),
    ("SSFFUEL", "Words", "library", "1 U238"),
    ("SSFGEOMETRY", "Numbers", "library", "1 1.0"),
    ("SSFMASS", "Words", "library", "1 1.0"),
    ("STEP", "Zero", "inventory", ""),
    ("TAB1", "Numbers", "initial", "1"),
    ("TAB2", "Numbers", "initial", "1"),
    ("TAB3", "Numbers", "initial", "1"),
    ("TAB4", "Numbers", "initial", "1"),
    ("TIME", "Special", "inventory", "2 HOURS"),
    ("TOLERANCE", "Numbers", "library", "0 1e1 2e-8"),
    ("UNCERTAINTY", "Numbers", "initial", "2"),
    ("UNCTYPE", "Numbers", "initial", "1"),
    ("USEFISSION", "Zero", "library", ""),
    ("USESPALLATION", "Words", "library", "1"),
    ("USEXSEXTRA", "Zero", "library", ""),
    ("WALL", "Numbers", "inventory", "1.0"),
    ("XSTHRESHOLD", "Numbers", "library", "1e-12"),
    ("ZERO", "Zero", "inventory", ""),
]
KEYWORDS = [(name, arity) for name, arity, _, _ in KEYWORD_TABLE]
SPEC_OF = {name: (arity, phase, args) for name, arity, phase, args in KEYWORD_TABLE}

# ------------------------------------------------------------------------------ port of the importer's lexer and parser
# fispact_import.rs: lex, parse_number, time_unit, resolve_keyword, Parser::parse_nodes. Rust's trim and is_whitespace
# use Unicode White_Space, which differs from Python's str.isspace on a few control characters.

RUST_WS = frozenset(" \t\n\x0b\x0c\r\x85\xa0            "
                    "    　")
RUST_FLOAT = re.compile(r"^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$")


class ModelError(Exception):
    """The deck the importer's parser refuses (the same error cases)."""


def rust_lstrip(text):
    i = 0
    while i < len(text) and text[i] in RUST_WS:
        i += 1
    return text[i:]


def rust_strip(text):
    text = rust_lstrip(text)
    j = len(text)
    while j > 0 and text[j - 1] in RUST_WS:
        j -= 1
    return text[:j]


def rust_lines(text):
    """str::lines: split at \\n, dropping one \\r before it; a lone \\r is not a line break."""
    if text == "":
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
        terminated = len(parts)
    else:
        terminated = len(parts) - 1
    return [p[:-1] if i < terminated and p.endswith("\r") else p for i, p in enumerate(parts)]


def ascii_upper(text):
    return "".join(c.upper() if "a" <= c <= "z" else c for c in text)


def lex(text):
    """Tokens (text, line, star) and the unterminated-comment error, as fispact_import::lex."""
    tokens, comment_from = [], None
    for index, raw in enumerate(rust_lines(text)):
        line, rest = index + 1, raw
        if comment_from is None:
            stripped = rust_lstrip(rest)
            if stripped.startswith("*"):
                tokens.append((rust_strip(stripped[1:]), line, True))
                continue
        while True:
            if comment_from is not None:
                position = rest.find(">>")
                if position < 0:
                    break
                rest, comment_from = rest[position + 2:], None
                continue
            rest = rust_lstrip(rest)
            if not rest:
                break
            if rest.startswith("<<"):
                comment_from, rest = line, rest[2:]
                continue
            end = len(rest)
            for position, character in enumerate(rest):
                if character in RUST_WS or rest.startswith("<<", position):
                    end = position
                    break
            tokens.append((rest[:end], line, False))
            rest = rest[end:]
    error = None if comment_from is None else f"line {comment_from}: '<<' comment is never closed by '>>'"
    return tokens, error


def parse_number(token):
    """A Fortran-style real: 1.0E-3, 2D5, 1.0+10 (exponent letter dropped); non-finite values are not numbers."""
    normalised = token.replace("D", "E").replace("d", "E")
    value = None
    if RUST_FLOAT.match(normalised):
        value = float(normalised)
    else:
        for index in range(1, len(normalised)):
            if normalised[index] in "+-" and (normalised[index - 1].isascii() and normalised[index - 1] in "0123456789."):
                candidate = normalised[:index] + "E" + normalised[index:]
                if RUST_FLOAT.match(candidate):
                    value = float(candidate)
                break
    if value is None or not math.isfinite(value):
        return None
    return value


def time_unit(token):
    key = ascii_upper(token)[:4]
    if key in ("SEC", "SECS", "SECO", "MIN", "MINS", "MINU", "HOU", "HOUR", "HR", "HRS", "DAY", "DAYS", "YEAR", "YR",
               "YRS"):
        return key
    return None


KEYWORD_ARITY = dict(KEYWORDS)


def resolve_keyword(token):
    upper = ascii_upper(token)
    if upper in KEYWORD_ARITY:
        return upper
    if len(upper) < 4:
        return None
    prefixed = [name for name, _ in KEYWORDS if name.startswith(upper)]
    if len(prefixed) == 1:
        return prefixed[0]
    head = upper[:4]
    sharing = [name for name, _ in KEYWORDS if len(name) >= 4 and name.startswith(head)]
    return sharing[0] if len(sharing) == 1 else None


class Model:
    """What the importer's parser makes of a deck: the keyword occurrences it must report."""

    def __init__(self):
        self.occurrences = []          # (line, keyword, phase) that must have an entry
        self.unreported = []           # (line, keyword, why) the importer does not report by design
        self.projectile = None         # first PROJECTILE code, if numeric
        self.tokens = 0


def parse_model(text):
    """Port of Parser::parse_nodes plus the order in which Interp::run_nodes logs. Iterative, so a deck nested very
    deeply does not exhaust this script's own stack."""
    tokens, lex_error = lex(text)
    model = Model()
    model.tokens = len(tokens)
    pos, phase, ended = 0, "library", False
    # blocks: each is a list of nodes; nodes are ("cmd", line, keyword, phase) or ("pulse", line, phase, count, body)
    blocks, pulses = [[]], []

    def peek():
        return tokens[pos] if pos < len(tokens) and not tokens[pos][2] else None

    def take_numbers(limit):
        nonlocal pos
        out = []
        while len(out) < limit:
            token = peek()
            if token is not None and parse_number(token[0]) is not None:
                out.append(token[0])
                pos += 1
            else:
                break
        return out

    def take_words():
        nonlocal pos
        out = []
        while True:
            token = peek()
            if token is None or resolve_keyword(token[0]) is not None:
                break
            out.append(token[0])
            pos += 1
        return out

    while pos < len(tokens) and not ended:
        text_, line, star = tokens[pos]
        pos += 1
        if star:
            blocks[-1].append(("cmd", line, "*", phase))
            continue
        name = resolve_keyword(text_)
        if name is None:
            take_numbers(1 << 60)
            blocks[-1].append(("cmd", line, ascii_upper(text_), phase))
            continue
        arity = KEYWORD_ARITY[name]
        if name == "ENDPULSE":
            if not pulses:
                raise ModelError(f"line {line}: ENDPULSE without a matching PULSE")
            body = blocks.pop()
            pline, pphase, count = pulses.pop()
            blocks[-1].append(("pulse", pline, pphase, count, body))
        elif name == "PULSE":
            args = take_numbers(1)
            value = parse_number(args[0]) if args else None
            if value is None or not (value >= 0.0 and value == math.floor(value) and value <= 1.0e9):
                raise ModelError(f"line {line}: PULSE needs a whole-number repeat count")
            pulses.append((line, phase, int(value)))
            blocks.append([])
        elif name == "END":
            if pos < len(tokens) and tokens[pos][2]:
                pos += 1
            blocks[-1].append(("cmd", line, name, phase))
            ended = True
        elif name == "FISPACT":
            if pos < len(tokens) and tokens[pos][2]:
                pos += 1
            blocks[-1].append(("cmd", line, name, phase))
            phase = "initial"
        elif name == "TIME":
            args = take_numbers(1)
            if not args:
                raise ModelError(f"line {line}: TIME needs a duration")
            token = peek()
            if token is not None and time_unit(token[0]) is not None:
                pos += 1
            blocks[-1].append(("cmd", line, name, phase))
            phase = "inventory"
        elif name in ("MASS", "FUEL"):
            want = 2 if name == "MASS" else 1
            args = take_numbers(want)
            if len(args) != want:
                raise ModelError(f"line {line}: {name} needs its leading numbers")
            count = parse_number(args[want - 1])
            if count is None or not (count >= 1.0 and count == math.floor(count) and count <= 10000.0):
                raise ModelError(f"line {line}: {name} count must be a positive whole number")
            for _ in range(2 * int(count)):
                if peek() is None:
                    raise ModelError(f"line {line}: {name} lists fewer entries than its count declares")
                pos += 1
            blocks[-1].append(("cmd", line, name, phase))
        else:
            if arity == "Numbers":
                take_numbers(1 << 60)
            elif arity == "Words":
                take_words()
            blocks[-1].append(("cmd", line, name, phase))
    if pulses:
        raise ModelError(f"line {pulses[-1][0]}: PULSE is not closed by ENDPULSE before the deck ends")
    if not ended and lex_error:
        raise ModelError(lex_error)
    # nodes in order; the body of a PULSE that repeats zero times is not run, so not reported
    stack = [iter(blocks[0])]
    flags = [True]
    while stack:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            flags.pop()
            continue
        if node[0] == "cmd":
            _, line, keyword, node_phase = node
            if flags[-1]:
                model.occurrences.append((line, keyword, node_phase))
            else:
                model.unreported.append((line, keyword, "in a PULSE that repeats 0 times"))
        else:
            _, line, node_phase, count, body = node
            if flags[-1]:
                model.occurrences.append((line, "PULSE", node_phase))
            else:
                model.unreported.append((line, "PULSE", "in a PULSE that repeats 0 times"))
            stack.append(iter(body))
            flags.append(flags[-1] and count > 0)
    # the first numeric PROJECTILE code, for choosing a fluxes file
    for index, (text_, line, star) in enumerate(tokens):
        if not star and resolve_keyword(text_) == "PROJECTILE" and index + 1 < len(tokens) and not tokens[index + 1][2]:
            value = parse_number(tokens[index + 1][0])
            if value is not None:
                model.projectile = value
            break
    return model


# ------------------------------------------------------------------------------ helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def record_file(path):
    return {"path": str(path), "sha256": sha256_file(path)}


def run_cmd(argv, timeout, cwd=ROOT, env=None):
    """Run without a shell. Returns (exit code, stdout, stderr); the exit code is None on a timeout or OS error."""
    try:
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, b"", f"timed out after {timeout} s".encode()
    except OSError as error:
        return None, b"", f"{type(error).__name__}: {error}".encode()
    return proc.returncode, proc.stdout, proc.stderr


def tail(data, n=400):
    return data.decode(errors="replace")[-n:]


def first_line(data, n=240):
    for line in data.decode(errors="replace").splitlines():
        if line.strip():
            return line.strip()[:n]
    return ""


def normalise_message(message):
    message = re.sub(r"/[^\s:'\"]+", "<path>", message)
    message = re.sub(r"'[^']*'", "'..'", message)
    return re.sub(r"[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?", "#", message)[:160]


def panic_signs(code, stdout, stderr):
    signs = []
    if code == 101:
        signs.append("exit code 101")
    if code is not None and code < 0:
        signs.append(f"killed by signal {-code}")
    text = stderr.decode(errors="replace") + stdout.decode(errors="replace")
    signs.extend(f"'{marker}' in the output" for marker in PANIC_MARKERS if marker in text)
    return signs


def report_path_of(out):
    out = str(out)
    return out[:-5] + ".import.json" if out.endswith(".json") else out + ".import.json"


def remove_outputs(*paths):
    for path in paths:
        for candidate in (Path(path), Path(report_path_of(path))):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


# ------------------------------------------------------------------------------ fluxes files


def flux_file_text(groups, bad=None):
    """A FISPACT-II fluxes file: `groups` values (highest energy first, 6 per line), the first-wall loading, a title."""
    values = [1.0e6 if groups * 4 // 10 <= i < groups * 7 // 10 else 0.0 for i in range(groups)]
    lines = []
    for start in range(0, groups, 6):
        lines.append(" " + "  ".join(f"{v:.4E}" for v in values[start:start + 6]))
    if bad == "non_numeric":
        lines[1] = lines[1].replace("0.0000E+00", "ABC", 1)
    wall = " 1.000"
    title = "Synthetic spectrum for the P140 importer checks"
    out = lines + ([] if bad == "no_wall" else [wall]) + ([] if bad == "no_title" else [title])
    if bad == "negative":
        out[0] = out[0].replace("0.0000E+00", "-1.0000E+00", 1)
    return "\n".join(out) + "\n"


FLUX_COUNTS = (66, 69, 100, 162, 172, 175, 211, 315, 351, 586, 709, 1102)
FLUX_BAD = ("non_numeric", "no_wall", "no_title", "negative", "empty")


def write_flux_files(directory):
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for count in FLUX_COUNTS:
        path = directory / f"fluxes_{count}"
        path.write_text(flux_file_text(count))
        paths[str(count)] = path
    for bad in FLUX_BAD:
        path = directory / f"fluxes_bad_{bad}"
        path.write_text("" if bad == "empty" else flux_file_text(709, bad))
        paths[f"bad_{bad}"] = path
    files = directory / "files_example"
    files.write_text("# synthetic files file\nxs_endf /nonexistent/gxs-709\nfluxes fluxes_709\narb_flux spectra\n")
    paths["files_ok"] = files
    files = directory / "files_nofluxes"
    files.write_text("# no fluxes entry\nxs_endf /nonexistent/gxs-709\ndk_endf /nonexistent/decay\n")
    paths["files_nofluxes"] = files
    files = directory / "files_missing_fluxes"
    files.write_text("fluxes no_such_fluxes_file\n")
    paths["files_missing_fluxes"] = files
    return paths


# ------------------------------------------------------------------------------ synthetic decks


def skeleton(lib=(), init=(), inv=(), material=True, fluxline="FLUX 1.0E+10"):
    lines = list(lib) + ["FISPACT", "* synthetic deck"]
    if material:
        lines += ["MASS 1.0E-3 1", "FE 100.0"]
    lines += list(init) + [fluxline, "TIME 1.0 HOURS", "ATOMS"] + list(inv)
    lines += ["FLUX 0.", "ZERO", "TIME 1.0 DAYS", "ATOMS", "END", "* end"]
    return lines


def lines_for(name, spelled, args):
    """The deck lines that carry keyword `name` (written `spelled`) with argument text `args`, plus the lines the
    keyword needs after it for the deck to be otherwise sound."""
    head = f"{spelled} {args}".rstrip() if args != "" else spelled
    if name == "MASS":
        return [head, "FE 100.0"]
    if name == "FUEL":
        return [head, "FE56 1.0E+20"]
    if name == "TIME":
        return [head, "ATOMS"]
    if name in ("ATOMS", "STEP", "SPECTRUM"):
        return ["TIME 5 MINS", head]
    if name == "PULSE":
        return [head, "TIME 10 SECS", "ATOMS", "ENDPULSE"]
    if name == "ENDPULSE":
        return ["PULSE 1", "TIME 10 SECS", "ATOMS", head]
    if name == "ZERO":
        return ["FLUX 0.", head]
    if name in ("FLUX", "POWER", "WALL", "PARTITION"):
        return [head, "TIME 5 MINS", "ATOMS"]
    if name == "FISPACT":
        return [head, "* second title"]
    if name == "END":
        return [head, "* end title"]
    return [head]


def place(name, lines, phase):
    slot = {"library": "lib", "initial": "init", "inventory": "inv"}[phase]
    return skeleton(material=name not in ("MASS", "FUEL"), **{slot: lines})


MALFORMED_ARGS = [
    "", "XYZ", "1 XYZ", "-1", "-1.0E+10", "0", "0.0", "1.5", "1e308", "1e309", "-1e308", "1e-320", "1.0D+3",
    "1.0+3", "1.0-3", "1.D0", "1.0E", "+.5", "inf", "nan", "1,5", "'", "-", "1 2 3 4 5 6 7 8 9",
    "1e308 1e308 1e308", "FE56 FE56 FE56", "FE 100.0 XYZ 1e", "1e308 SECS", "0 SECS", "-5 SECS", "5 FORTNIGHTS",
    "1000000000", "9999999999", "4294967296", "0.5 0.5", "1 FE 2 XX 3",
]


def mixed_case(name):
    return "".join(c.lower() if i % 2 else c for i, c in enumerate(name))


def abbreviations(name):
    forms = {name.lower(), mixed_case(name)}
    if len(name) > 4:
        forms.add(name[:4])
        forms.add(name[:4].lower())
        forms.add(name[: max(4, (len(name) + 1) // 2 + 1)])
        forms.add(name[:-1])
    if len(name) >= 4:
        forms.add(name[:3])
    forms.add(name + "X")
    forms.add(name + "ES")
    forms.discard(name)
    return sorted(forms)


AMBIGUOUS_WORDS = [
    "NOT", "NOT5", "NOXR", "NOS", "NOSO", "NOST", "NOF", "NOFI", "NOC", "NOCO", "NOE", "NOER", "SSF", "SSFM", "SSFG",
    "SSFC", "SSFD", "SSFF", "TAB", "TAB5", "GET", "GETD", "GETX", "GRP", "GRAP", "GROU", "PATH", "PATHR", "PAT", "SPEC",
    "SPE", "SPEK", "ENDP", "END", "ENDX", "ENDPU", "FISC", "FISH", "FISPAC", "FIS", "ZER", "ZERO0", "UNC", "UNCE", "UNCT",
    "UNCERT", "USE", "USEF", "USES", "USEX", "TOL", "TOLER", "TOLERANCES", "FLUXES", "FLUXE", "SPECTRA", "HAZA", "HAZ",
    "COVA", "COV", "CLE", "CLEA", "CLO", "CLOB", "MASSES", "MAS", "TIMES", "TIM", "STEPS", "STE", "ATOM", "ATO", "ATW",
    "PRO", "PROB", "PROJ", "PROJECT", "PART", "PARTI", "POW", "POWE", "ROUT", "RES", "RESU", "SORT", "SORTD",
]


def build_synthetic(fluxes):
    """All synthetic decks: dicts with id, category, group, data (bytes), flux (key into `fluxes` or None), args."""
    decks = []

    def add(category, ident, text, flux="709", args=(), expect=None, timeout=None):
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        decks.append({"id": ident, "category": category, "group": f"synthetic/{category}", "data": data, "flux": flux,
                      "args": list(args), "expect": expect, "timeout": timeout})

    def lines(*parts):
        return "\n".join(parts) + "\n"

    for name, arity, phase, args in KEYWORD_TABLE:
        deck = place(name, lines_for(name, name, args), phase)
        add("keyword_valid", f"valid_{name}", "\n".join(deck) + "\n")
        for other in PHASES:
            if other != phase:
                deck = place(name, lines_for(name, name, args), other)
                add("keyword_wrong_phase", f"phase_{other}_{name}", "\n".join(deck) + "\n")
        for index, bad in enumerate(MALFORMED_ARGS):
            deck = place(name, lines_for(name, name, bad), phase)
            add("keyword_malformed", f"malformed_{name}_{index:02d}", "\n".join(deck) + "\n")
        for index, spelled in enumerate(abbreviations(name)):
            deck = place(name, lines_for(name, spelled, args), phase)
            add("keyword_abbrev", f"abbrev_{name}_{index:02d}_{re.sub('[^A-Za-z0-9]', '_', spelled)}",
                "\n".join(deck) + "\n")
    for index, word in enumerate(AMBIGUOUS_WORDS):
        deck = skeleton(init=[f"{word} 1"])
        add("keyword_abbrev", f"ambiguous_{index:02d}_{word}", "\n".join(deck) + "\n")

    # ---- combinations
    head = ["CLOBBER", "GETXS 0", "GETDECAY 0"]
    add("combo", "power_density", lines(
        *head, "FISPACT", "* POWER and DENSITY", "DENSITY 7.875", "MASS 1.0E-3 2", "FE 98.0", "CR 2.0",
        "POWER 1.0E-3 1 301", "TIME 1 HOURS", "ATOMS", "POWER 0.0", "TIME 1 DAYS ATOMS", "ZERO", "TIME 10 DAYS ATOMS",
        "END", "* end"))
    add("combo", "power_no_density", lines(
        *head, "FISPACT", "* POWER without DENSITY", "MASS 1.0E-3 1", "FE 100.0", "POWER 2.5 1 301", "TIME 10 MINS", "ATOMS",
        "END", "* end"))
    add("combo", "power_then_flux_then_wall", lines(
        *head, "FISPACT", "* drives", "DENSITY 7.875", "MASS 1.0E-3 1", "FE 100.0", "POWER 1.0 1 301", "TIME 1 HOURS",
        "ATOMS", "FLUX 1.0E+10", "TIME 1 HOURS", "ATOMS", "WALL 1.5", "TIME 1 HOURS", "ATOMS", "POWER 0", "TIME 1 DAYS",
        "ATOMS", "END", "* end"))
    add("combo", "power_other_mts", lines(
        *head, "FISPACT", "* POWER on several MTs", "DENSITY 2.0", "MASS 1.0E-3 1", "FE 100.0", "POWER 1.0 3 301 102 4",
        "TIME 1 HOURS", "ATOMS", "END", "* end"))
    add("combo", "pulse_nested_zero", lines(
        *head, "FISPACT", "* nested pulses", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 3", "TIME 10 SECS ATOMS",
        "FLUX 0.", "TIME 20 SECS ATOMS", "FLUX 1.0E+10", "PULSE 2", "TIME 1 SECS ATOMS", "ENDPULSE", "ENDPULSE", "FLUX 0.",
        "ZERO", "TIME 1 DAYS ATOMS", "END", "* end"))
    add("combo", "pulse_zero_inside", lines(
        *head, "FISPACT", "* ZERO inside a pulse", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 2", "TIME 10 SECS ATOMS",
        "ZERO", "TIME 20 SECS ATOMS", "ENDPULSE", "TIME 1 DAYS ATOMS", "END", "* end"))
    add("combo", "pulse_zero_repeats", lines(
        *head, "FISPACT", "* zero-repeat pulse", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "PULSE 0", "TIME 10 SECS ATOMS", "HALF", "FLUX 0.", "ENDPULSE", "TIME 1 DAYS ATOMS", "END", "* end"))
    add("combo", "pulse_keywords_inside", lines(
        *head, "FISPACT", "* keywords in a pulse body", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 4", "NOSTABLE",
        "PARTITION 1 FE 0.9", "TIME 10 SECS", "ATOMS", "FLUX 0.", "TIME 5 SECS", "STEP", "FLUX 1.0E+10", "ENDPULSE", "END",
        "* end"))
    add("combo", "pulse_unclosed", lines(
        *head, "FISPACT", "* open pulse", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 2", "TIME 10 SECS ATOMS",
        "END", "* end"))
    add("combo", "endpulse_alone", lines(
        *head, "FISPACT", "* stray ENDPULSE", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 10 SECS ATOMS", "ENDPULSE",
        "END", "* end"))
    add("combo", "time_units", lines(
        *head, "FISPACT", "* every time unit", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 SECS ATOMS",
        "TIME 1 SEC ATOMS", "TIME 1 SECONDS ATOMS", "TIME 2 MINS ATOMS", "TIME 2 MIN ATOMS", "TIME 2 MINUTES ATOMS",
        "TIME 3 HOURS ATOMS", "TIME 3 HOU ATOMS", "TIME 3 HRS ATOMS", "TIME 3 HR ATOMS", "TIME 4 DAYS ATOMS",
        "TIME 4 DAY ATOMS", "TIME 1 YEARS ATOMS", "TIME 1 YEAR ATOMS", "TIME 1 YRS ATOMS", "TIME 1 YR ATOMS", "TIME 5 ATOMS",
        "TIME 1.5E+3 secs ATOMS", "TIME 1.0D+2 Mins ATOMS", "TIME 1.0+2 days ATOMS", "FLUX 0.", "ZERO", "TIME 100 YEARS ATOMS",
        "END", "* end"))
    add("combo", "time_many_steps", lines(
        *head, "FISPACT", "* many steps", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10",
        *[f"TIME {1 + i} {('SECS', 'MINS', 'HOURS', 'DAYS')[i % 4]} {('ATOMS', 'STEP', 'SPECTRUM')[i % 3]}" for i in range(60)],
        "END", "* end"))
    add("combo", "time_without_trigger", lines(
        *head, "FISPACT", "* TIME with no output keyword", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS",
        "TIME 2 HOURS", "ATOMS", "TIME 3 HOURS", "END", "* end"))
    add("combo", "over_several", lines(
        *head, "FISPACT", "* OVER sub-keywords", "MASS 1.0E-3 2", "FE 90.0", "NI 10.0", "OVER FE56", "  ACROSS FE57 0.5",
        "  ACROSS FE55 1.5", "  ALAM 1.0E+3 DAYS", "  ADCROSS FE57 1.1", "  ADLAM 1.2", "OVER NI58", "  ACROSS NI57 0.0",
        "  ACROSS CO58 0.1", "  ALAM 70 DAYS", "  ALAM 10 HOURS", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "over_conflicts", lines(
        *head, "FISPACT", "* OVER conflicts", "MASS 1.0E-3 1", "FE 100.0", "OVER FE56 ACROSS FE57 0.5", "OVER FE56 ACROSS FE57 0.7",
        "OVER FE56 ALAM 0.0", "OVER FE56 ALAM -3", "OVER ACROSS FE57 1.0", "OVER ALAM", "OVER XX99 ALAM 1", "OVER",
        "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "path_routes_uncertainty", lines(
        *head, "FISPACT", "* PATH and ROUTES with UNCERTAINTY", "MASS 1.0E-3 1", "FE 100.0", "UNCERTAINTY 2",
        "PATH 2 MN54 CO60", "ROUTES MN54", "PATHRESET 1", "XSTHRESHOLD 1e-12", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "FLUX 0.", "ZERO", "TIME 1 DAYS ATOMS", "END", "* end"))
    add("combo", "uncertainty_levels", lines(
        *head, "FISPACT", "* UNCERTAINTY forms", "MASS 1.0E-3 1", "FE 100.0", "UNCERTAINTY 0", "UNCERTAINTY -1",
        "UNCERTAINTY -1 1 2 3 2", "UNCERTAINTY 3", "UNCERTAINTY 4", "UNCTYPE 1", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END",
        "* end"))
    add("combo", "grpconvert_709", lines(
        *head, "GRPCONVERT 709 709", "FISPACT", "* GRPCONVERT on the library structure", "MASS 1.0E-3 1", "FE 100.0",
        "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    for count in (66, 69, 100, 172, 175, 211, 315, 351):
        add("combo", f"grpconvert_{count}", lines(
            *head, f"GRPCONVERT {count} 709", "CNVTYPE 0", "LIMGRP 1.0E+5 1.4E+7", "FISPACT", f"* {count}-group spectrum",
            "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"), flux=str(count))
    add("combo", "grpconvert_without_flux_file", lines(
        *head, "GRPCONVERT 175 709", "CNVTYPE 1", "FISPACT", "* no spectrum to convert", "MASS 1.0E-3 1", "FE 100.0",
        "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"), flux=None)
    add("combo", "grpconvert_late", lines(
        *head, "FISPACT", "* conversion after TIME", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "GRPCONVERT 175 709", "CNVTYPE 1", "LIMGRP 1 2", "END", "* end"), flux="175")
    add("combo", "limgrp_bad", lines(
        *head, "LIMGRP 5 1", "LIMGRP 0 1", "LIMGRP -1 -2", "LIMGRP 1", "CNVTYPE 2", "FISPACT", "* bad limits", "MASS 1.0E-3 1",
        "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "grpconvert_flux_mismatch", lines(
        *head, "GETXS 1 175", "FISPACT", "* GETXS group count against the flux", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10",
        "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "flux_wrong_group_count", lines(
        *head, "FISPACT", "* 100-group spectrum, no GRPCONVERT", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "END", "* end"), flux="100")
    for code in (0, 1, 2, 3, 4, 5, 6, 7, 8, -1, 1.5):
        add("combo", f"projectile_{str(code).replace('.', 'p').replace('-', 'm')}", lines(
            "CLOBBER", f"PROJECTILE {code}", "GETXS 1 162", "GETDECAY 0", "FISPACT", "* projectile", "MASS 1.0E-3 1", "Y 100.0",
            "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "FLUX 0.", "TIME 1 DAYS ATOMS", "END", "* end"), flux="162")
    add("combo", "projectile_proton_with_neutron_flux", lines(
        "PROJECTILE 3", "FISPACT", "* proton, 709 groups", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "END", "* end"), flux="709")
    add("combo", "projectile_late", lines(
        *head, "FISPACT", "* PROJECTILE after FISPACT", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PROJECTILE 3",
        "TIME 1 HOURS ATOMS", "PROJECTILE 1", "END", "* end"))
    add("combo", "fuel_fission", lines(
        "CLOBBER", "NOHEAD", "GETXS 0", "GETDECAY 0", "FISPACT", "*Uranium 235 fission pulse", "USEFISSION", "FISYIELD 1 U235",
        "FUEL 1", "  U235 1.0E+24", "MIND 1.0", "DENSITY 19.0", "HAZA", "HALF", "OVER U235", "  ACROSS U236 0.0",
        "UNCERT 2", "TOL 0 1E-2 1E-6", "SORTDOMINANT 100 100", "FISCHOOSE 1 U235", "ATOMS", "FLUX 1.0E+14", "TIME 2.0E+4",
        "ATOMS", "FLUX 0.", "ZERO", "END", "* end"))
    add("combo", "fuel_many", lines(
        *head, "FISPACT", "* FUEL with several nuclides", "FUEL 4", "FE56 1.0E+20", "FE54 5.0E+18", "NI58 2.0E+19", "CO59 1.0E+17",
        "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "fuel_and_mass", lines(
        *head, "FISPACT", "* FUEL and MASS together", "MASS 1.0E-3 1", "FE 100.0", "FUEL 1", "FE56 1.0E+20", "FLUX 1.0E+10",
        "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "mass_variants", lines(
        *head, "FISPACT", "* MASS on one line", "MASS 1.0 3 Ti 80.0 Fe 14.8 Cr 5.2", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END",
        "* end"))
    add("combo", "mass_percent_not_100", lines(
        *head, "FISPACT", "* percentages", "MASS 1.0E-3 2", "FE 60.0", "FE 30.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END",
        "* end"))
    add("combo", "mass_bad_element", lines(
        *head, "FISPACT", "* bad element", "MASS 1.0E-3 1", "XX 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "mass_negative_percent", lines(
        *head, "FISPACT", "* negative percent", "MASS 1.0E-3 1", "FE -5.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "mass_count_huge", lines(
        *head, "FISPACT", "* count too large", "MASS 1.0E-3 1000000000", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END",
        "* end"))
    add("combo", "mass_nuclide_name", lines(
        *head, "FISPACT", "* nuclide in MASS", "MASS 1.0E-3 1", "FE56 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "no_material", lines(
        *head, "FISPACT", "* no material", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "no_time_step", lines(
        *head, "FISPACT", "* no TIME", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "ATOMS", "END", "* end"))
    add("combo", "library_only", lines("CLOBBER", "GETXS 1 709", "GETDECAY 1", "PRINTLIB 1", "END", "* end"))
    add("combo", "partition_wall_dose", lines(
        *head, "FISPACT", "* PARTITION, WALL, DOSE, GROUP, BREMSSTRAHLUNG, ATWO", "MASS 1.0E-3 2", "FE 90.0", "NI 10.0", "DOSE 2 0.5",
        "GROUP 1", "BREMSSTRAHLUNG 2 CO60 MN54", "ATWO", "HAZA", "WALL 1.5", "TIME 1 HOURS ATOMS", "PARTITION 1 NI 0.0",
        "TIME 1 HOURS ATOMS", "PARTITION 2 FE 0.5 NI 0.5", "WALL 0", "TIME 1 DAYS ATOMS", "PARTITION FE 1.0", "END", "* end"))
    add("combo", "partition_bad", lines(
        *head, "FISPACT", "* bad partitions", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PARTITION 3 FE 0.5", "PARTITION FE",
        "PARTITION FE 2.0", "PARTITION XX 0.5", "PARTITION", "PARTITION 0", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "dose_modes", lines(
        *head, "FISPACT", "* DOSE geometries", "MASS 1.0E-3 1", "FE 100.0", "DOSE", "DOSE 1", "DOSE 2 1e-9", "DOSE 2 0.0",
        "DOSE 2", "DOSE 3", "DOSE 1.5", "DOSE -1", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "DOSE 2 2.0", "END", "* end"))
    add("combo", "group_and_brems_bad", lines(
        *head, "FISPACT", "* photon keywords", "MASS 1.0E-3 1", "FE 100.0", "GROUP 5", "GROUP", "BREMSSTRAHLUNG", "BREMSSTRAHLUNG 3 CO60",
        "BREMSSTRAHLUNG FE", "BREMSSTRAHLUNG XX1", "BREMSSTRAHLUNG 1 CO60", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("combo", "summary_keywords", lines(
        *head, "FISPACT", "* summary keywords", "MASS 1.0E-3 1", "FE 100.0", "MIND 1.E3", "NOSTABLE", "SORTDOMINANT 100 20",
        "SORTDOMINANT 5", "SORTDOMINANT 0", "SORTDOMINANT 1.5", "SORTDOMINANT -3 -3", "NOERROR", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS",
        "NOSTABLE", "MIND 10", "END", "* end"))
    add("combo", "getxs_getdecay_inventory", lines(
        *head, "FISPACT", "* mid-run data", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "GETXS 1 709",
        "GETDECAY 1", "MASS 1.0E-3 1", "FE 100.0", "FUEL 1", "FE56 1.0E+10", "TIME 1 DAYS ATOMS", "END", "* end"))
    add("combo", "unknown_keywords", lines(
        *head, "FISPACT", "* unknown words", "MASS 1.0E-3 1", "FE 100.0", "FROBNICATE 1 2 3", "XYZZY", "FLUX 1.0E+10",
        "TIME 1 HOURS ATOMS", "PLUGH 7", "END", "* end"))
    add("combo", "unknown_words_after_arity", lines(
        *head, "FISPACT", "* unknown words after Zero and Numbers keywords", "MASS 1.0E-3 1", "FE 100.0", "HALF hello world",
        "DENSITY 7.875 g/cc", "MIND 1.E3 atoms", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    every = []
    for name, arity, phase, args in KEYWORD_TABLE:
        if name in ("MASS", "FUEL", "FISPACT", "END", "TIME", "ATOMS", "STEP", "SPECTRUM", "PULSE", "ENDPULSE", "ZERO", "FLUX",
                    "POWER", "WALL", "PARTITION", "PROJECTILE", "GETXS", "GRPCONVERT", "LIMGRP", "CNVTYPE"):
            continue
        every.append((phase, f"{name} {args}".rstrip()))
    library = [text for phase, text in every if phase == "library"]
    initial = [text for phase, text in every if phase == "initial"]
    add("combo", "every_keyword", lines(
        *library, "FISPACT", "* every keyword", "MASS 1.0E-3 1", "FE 100.0", *initial, "FLUX 1.0E+10", "TIME 1 HOURS", "ATOMS",
        "PARTITION 1 FE 0.9", "WALL 1.0", "TIME 1 HOURS", "STEP", "POWER 0", "TIME 1 HOURS", "SPECTRUM", "FLUX 0.", "ZERO",
        "TIME 1 DAYS", "ATOMS", "END", "* end"))
    add("combo", "every_keyword_inventory", lines(
        "FISPACT", "* every keyword, after the first TIME", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS", "ATOMS",
        *[text for phase, text in every], "TIME 1 DAYS", "ATOMS", "END", "* end"))
    add("combo", "all_on_one_line", "CLOBBER GETXS 0 GETDECAY 0 FISPACT\n* one-line deck\nMASS 1.0E-3 1 FE 100.0 FLUX 1.0E+10 "
                                    "TIME 1 HOURS ATOMS FLUX 0. ZERO TIME 1 DAYS ATOMS END\n* end\n")
    add("combo", "args_on_separate_lines", lines(
        *head, "FISPACT", "* arguments split over lines", "MASS", "1.0E-3", "1", "FE", "100.0", "FLUX", "1.0E+10", "TIME", "1",
        "HOURS", "ATOMS", "TIME", "1", "DAYS", "ATOMS", "END", "* end"))

    # ---- lexical
    base = lambda *init: "\n".join(skeleton(init=list(init))) + "\n"
    add("lexical", "comment_multiline", lines(
        "CLOBBER", "<< a comment that", "FLUX 99 MASS 1 4 DOES NOT COUNT", "ZERO ZERO", ">> GETXS 0", "GETDECAY 0", "FISPACT", "* t",
        "MASS 1.0E-3 1", "FE 100.0", "<< line one", "line two", "line three >> FLUX 1.0E+10 << trailing >>", "TIME 1 HOURS ATOMS", "END",
        "* end"))
    add("lexical", "comment_adjacent", lines(
        "CLOBBER", "FISPACT", "* t", "MASS<<c>>1.0E-3<<c>>1", "FE<<c>>100.0", "FLUX<<x>>1.0E+10", "TIME 1<<c>>HOURS ATOMS<<c>>", "END<<c>>",
        "* end"))
    add("lexical", "comment_marker_in_word", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0<<c>>", "FLUX 1.0E+10<<", ">>", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("lexical", "comment_star_inside", lines(
        "CLOBBER", "<<", "* not a title line", ">>", "FISPACT", "* t << not a comment >> FLUX 3", "MASS 1.0E-3 1", "FE 100.0",
        "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end"))
    add("lexical", "comment_unterminated_before_end", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "<< never closed", "END", "* end"))
    add("lexical", "comment_unterminated_after_end", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end", "<< never closed"))
    add("lexical", "comment_unterminated_no_end", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "<< never closed"))
    add("lexical", "comment_nested_open", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "<< outer << inner >> FLUX 3.0E+10 >>", "FLUX 1.0E+10",
        "TIME 1 HOURS ATOMS", "END", "* end"))
    add("lexical", "comment_stray_close", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", ">> FLUX 1.0E+10", "TIME 1 HOURS ATOMS", ">>", "END", "* end"))
    add("lexical", "comment_only_deck", lines("<< nothing here >>", "<< at all", "really >>"))
    add("lexical", "comment_keyword_in_comment_after_end", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "TIME 1 HOURS ATOMS", "END", "* end", "<< FLUX 3 >>",
        "TIME garbage"))
    add("lexical", "tabs_separators", "CLOBBER\nFISPACT\n*\ttitle\twith tabs\nMASS\t1.0E-3\t1\nFE\t100.0\n\tFLUX\t1.0E+10\n"
                                       "TIME\t1\tHOURS\tATOMS\nEND\n*\tend\n")
    add("lexical", "tabs_indented_star", "CLOBBER\n\t\t* tab-indented title\nFISPACT\n\t* tab-indented\nMASS 1.0E-3 1\nFE 100.0\n"
                                          "FLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n  * end\n")
    add("lexical", "formfeed_vtab", "CLOBBER\nFISPACT\n* t\nMASS\x0b1.0E-3\x0c1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "crlf_all", "\r\n".join(skeleton()) + "\r\n")
    add("lexical", "crlf_mixed", "CLOBBER\r\nFISPACT\n* t\r\nMASS 1.0E-3 1\nFE 100.0\r\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\r\nEND\n* end\r\n")
    add("lexical", "crlf_in_comment", "CLOBBER\r\n<< comment\r\nspanning >> FISPACT\r\n* t\r\nMASS 1.0E-3 1\r\nFE 100.0\r\n"
                                       "FLUX 1.0E+10\r\nTIME 1 HOURS ATOMS\r\nEND\r\n* end\r\n")
    add("lexical", "lone_cr", "CLOBBER\rFISPACT\r* t\rMASS 1.0E-3 1\rFE 100.0\rFLUX 1.0E+10\rTIME 1 HOURS ATOMS\rEND\r* end\r")
    add("lexical", "cr_before_eof", "\n".join(skeleton()) + "\r")
    add("lexical", "no_final_newline", "\n".join(skeleton()))
    add("lexical", "blank_lines_and_trailing_space", "\n\n  \nCLOBBER   \n\n\nFISPACT  \n* t   \n\nMASS 1.0E-3 1   \n  FE 100.0\n\n"
                                                      "FLUX 1.0E+10 \nTIME 1 HOURS ATOMS \n\nEND  \n* end  \n\n\n")
    add("lexical", "trailing_garbage_after_end", base() + "this is not a keyword\nTIME 7 DAYS ATOMS\n<< open\n\x00\x01\x02 binary \xff\n")
    add("lexical", "trailing_garbage_no_end", "\n".join(skeleton()[:-2]) + "\nthis is not a keyword 1 2 3\nQQ 7\n@#$% ^&*()\n")
    add("lexical", "garbage_between_keywords", "\n".join(skeleton(init=["@@@ ### 1.0", "%%%%", "- + * /", "1 2 3", "1.5E+3", "''", '"quoted"'])) + "\n")
    add("lexical", "nul_bytes", "CLOBBER\nFISPACT\n* t\x00t\nMASS 1.0E-3 1\nFE 100.0\nFLU\x00X 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "non_ascii_title", "CLOBBER\nFISPACT\n* tïtle ☃ 中文\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\n"
                                       "END\n* énd\n")
    add("lexical", "non_ascii_comment", "CLOBBER\n<< café ☃ >>\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\n"
                                         "END\n* end\n")
    add("lexical", "non_ascii_unknown_keyword", base("café 1 2", "☃", "ÜBER 7", "İNDEX 1"))
    add("lexical", "non_ascii_element", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFé 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "non_ascii_digits", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nFLUX ١.0E+10\nTIME １ HOURS ATOMS\nEND\n* end\n")
    add("lexical", "non_ascii_whitespace", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE　100.0\nFLUX 1.0E+10\n"
                                            "TIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "bom_first", "﻿" + "\n".join(skeleton(lib=["CLOBBER"])) + "\n")
    add("lexical", "invalid_utf8_latin1", b"CLOBBER\nFISPACT\n* caf\xe9\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "invalid_utf8_ff", b"\xff\xfe\xfd" + "\n".join(skeleton()).encode() + b"\n")
    add("lexical", "invalid_utf8_truncated", "\n".join(skeleton()).encode() + b"\n\xe2\x82")
    add("lexical", "empty_deck", "")
    add("lexical", "whitespace_only", "   \n\t\n  \r\n\n")
    add("lexical", "only_end", "END\n* end\n")
    add("lexical", "only_fispact", "FISPACT\n* t\n")
    add("lexical", "only_star", "* just a title\n")
    add("lexical", "no_end_keyword", "\n".join(skeleton()[:-2]) + "\n")
    add("lexical", "no_fispact", "MASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "two_fispact", "\n".join(skeleton(init=["FISPACT", "* again"])) + "\n")
    add("lexical", "two_end", "\n".join(skeleton()) + "\nEND\n* end2\n")
    add("lexical", "end_without_title", "\n".join(skeleton()[:-1]) + "\n")
    add("lexical", "end_title_with_comment_marker", "\n".join(skeleton()[:-1]) + "\n* end << not a comment\nFLUX 7\n")
    add("lexical", "star_alone", "CLOBBER\nFISPACT\n*\nMASS 1.0E-3 1\nFE 100.0\n*\nFLUX 1.0E+10\n*END\nTIME 1 HOURS ATOMS\nEND\n*\n")
    add("lexical", "star_between_args", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3\n* between\n1\nFE 100.0\nFLUX\n* between\n1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "star_in_mass_body", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\n* star where the element should be\nFE 100.0\nFLUX 1.0E+10\n"
                                         "TIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "lowercase_deck", "\n".join(skeleton(lib=["clobber", "getxs 0"])).lower() + "\n")
    add("lexical", "mixed_case_deck", "\n".join(mixed_case(line) for line in skeleton(lib=["CLOBBER"])) + "\n")
    add("lexical", "fortran_numbers_in_deck", lines(
        "CLOBBER", "FISPACT", "* t", "MASS 1.0D-3 1", "FE 1.0D+2", "DENSITY 7.875D0", "FLUX 1.116+10", "TIME 5.0 MINS ATOMS", "FLUX 0.",
        "ZERO", "TIME 6.6D+1 ATOMS", "TIME 1.6+1 ATOMS", "TIME 1.D1 ATOMS", "END", "* end"))
    add("lexical", "very_long_line", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\n" + "HALF " * 20000 + "\nFLUX 1.0E+10\n"
                                       "TIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "very_long_word", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\n" + "A" * 2_000_000 + "\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\n"
                                      "END\n* end\n")
    add("lexical", "very_long_number", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nDENSITY " + "1" * 400 + "\nFLUX 1" + "0" * 400 +
        "\nTIME 1 HOURS ATOMS\nEND\n* end\n")
    add("lexical", "very_long_title", "CLOBBER\nFISPACT\n* " + "T" * 1_000_000 + "\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\n"
                                      "END\n* end\n")
    add("lexical", "many_blank_lines", "\n" * 500000 + "\n".join(skeleton()) + "\n")
    add("lexical", "many_keywords", "CLOBBER\nFISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\n" + "HALF\n" * 20000 + "FLUX 1.0E+10\nTIME 1 HOURS ATOMS\n"
                                    "END\n* end\n")

    # ---- command line and files-file cases
    deck = "\n".join(skeleton(lib=["CLOBBER", "GETXS 0", "GETDECAY 0"])) + "\n"
    add("cli", "files_explicit", deck, flux=None, args=["--files", "{files_ok}"])
    add("cli", "files_without_fluxes_entry", deck, flux=None, args=["--files", "{files_nofluxes}"])
    add("cli", "files_names_missing_fluxes", deck, flux=None, args=["--files", "{files_missing_fluxes}"])
    add("cli", "files_file_missing", deck, flux=None, args=["--files", "/nonexistent/files"])
    add("cli", "files_and_fluxes_both", deck, flux="709", args=["--files", "{files_ok}"])
    add("cli", "fluxes_missing_file", deck, flux=None, args=["--fluxes", "/nonexistent/fluxes"])
    add("cli", "fluxes_directory", deck, flux=None, args=["--fluxes", "{fluxdir}"])
    add("cli", "fluxes_duplicate", deck, flux="709", args=["--fluxes", "{fluxes_709}"])
    for bad in FLUX_BAD:
        add("cli", f"fluxes_bad_{bad}", deck, flux=f"bad_{bad}")
    add("cli", "fluxes_100_groups", deck, flux="100")
    add("cli", "library_nonexistent", deck, flux="709", args=["--library", "/nonexistent/lib.npz"])
    add("cli", "decay_nonexistent", deck, flux="709", args=["--decay", "/nonexistent/decay"])
    add("cli", "covariance_nonexistent", deck, flux="709", args=["--covariance", "/nonexistent/cov.npz"])
    add("cli", "library_and_decay_given", deck, flux="709", args=["--library", "lib/some.npz", "--decay", "decay/some"])
    add("cli", "option_without_value", deck, flux="709", args=["--decay"])
    add("cli", "unknown_option", deck, flux="709", args=["--no-such-option"])
    add("cli", "extra_positional", deck, flux="709", args=["extra.json"])
    add("cli", "strict_given_explicitly", deck, flux="709", args=["--strict"])
    add("cli", "deck_missing", deck, flux="709", args=[], expect="deck_missing")
    add("cli", "deck_is_directory", deck, flux="709", args=[], expect="deck_directory")
    add("cli", "out_directory_missing", deck, flux="709", args=[], expect="out_missing_dir")
    add("cli", "out_without_json_suffix", deck, flux="709", args=[], expect="out_no_suffix")
    add("cli", "no_arguments", deck, flux=None, args=[], expect="no_args")

    # ---- stress
    add("stress", "pulse_empty_1e9", lines("FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 1000000000", "ENDPULSE",
                                           "TIME 1 HOURS ATOMS", "END", "* end"), timeout=STRESS_TIMEOUT)
    add("stress", "pulse_nested_empty_1e9", lines("FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 1000000000",
                                                  "PULSE 1000000000", "ENDPULSE", "ENDPULSE", "TIME 1 HOURS ATOMS", "END", "* end"),
        timeout=STRESS_TIMEOUT)
    add("stress", "pulse_steps_100000", lines("FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 100000",
                                               "TIME 1 SECS ATOMS", "ENDPULSE", "END", "* end"), timeout=STRESS_TIMEOUT)
    add("stress", "pulse_steps_100001", lines("FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 100001",
                                               "TIME 1 SECS ATOMS", "ENDPULSE", "END", "* end"), timeout=STRESS_TIMEOUT)
    add("stress", "pulse_nested_steps_1e6", lines("FISPACT", "* t", "MASS 1.0E-3 1", "FE 100.0", "FLUX 1.0E+10", "PULSE 1000",
                                                  "PULSE 1000", "TIME 1 SECS ATOMS", "ENDPULSE", "ENDPULSE", "END", "* end"),
        timeout=STRESS_TIMEOUT)
    for depth in (200, 5000, 100000):
        add("stress", f"pulse_depth_{depth}", "FISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\n" + "PULSE 1\n" * depth +
            "TIME 1 SECS ATOMS\n" + "ENDPULSE\n" * depth + "END\n* end\n", timeout=STRESS_TIMEOUT)
    add("stress", "time_steps_100000", "FISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\n" + "TIME 1 SECS ATOMS\n" * 100000 + "END\n* end\n",
        timeout=STRESS_TIMEOUT)
    add("stress", "time_steps_100001", "FISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nFLUX 1.0E+10\n" + "TIME 1 SECS ATOMS\n" * 100001 + "END\n* end\n",
        timeout=STRESS_TIMEOUT)
    add("stress", "mass_10000_elements", "FISPACT\n* t\nMASS 1.0E-3 10000\n" + "FE 0.01\n" * 10000 + "FLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n",
        timeout=STRESS_TIMEOUT)
    add("stress", "fuel_10000_nuclides", "FISPACT\n* t\nFUEL 10000\n" + "FE56 1.0E+10\n" * 10000 + "FLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n",
        timeout=STRESS_TIMEOUT)
    add("stress", "over_10000_subkeywords", "FISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nOVER FE56\n" + "ACROSS FE57 0.5\nALAM 10 DAYS\n" * 10000 +
        "FLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n", timeout=STRESS_TIMEOUT)
    add("stress", "path_10000_targets", "FISPACT\n* t\nMASS 1.0E-3 1\nFE 100.0\nPATH 10000\n" + "FE55 MN54 CO60 " * 3334 +
        "\nFLUX 1.0E+10\nTIME 1 HOURS ATOMS\nEND\n* end\n", timeout=STRESS_TIMEOUT)
    return decks


# ------------------------------------------------------------------------------ real decks


def collect_real(fns, fission, pypact):
    """Real decks: dicts with id, group, path, args (import options), validate policy."""
    decks = []
    for path in sorted(fns.glob("*/*.i")):
        files = path.with_name(f"{path.stem}_files")
        decks.append({"id": f"{path.parent.name}__{path.stem}", "group": "fns", "path": path,
                      "args": ["--files", str(files)] if files.is_file() else [], "validate_files": "required",
                      "validate": "required", "must_import": True})
    fluxes = fission / "fluxes_therm"
    for path in sorted(fission.glob("*.i")):
        decks.append({"id": f"fission__{path.stem}", "group": "fission", "path": path,
                      "args": ["--fluxes", str(fluxes)] if fluxes.is_file() else [], "validate_files": "recorded",
                      "validate": "required", "must_import": True})
    pypact_decks = sorted((pypact / "reference").glob("*.i")) + sorted((pypact / "examples" / "files").glob("test.i"))
    for path in pypact_decks:
        rel = path.relative_to(pypact)
        tag = "__".join(rel.with_suffix("").parts)
        decks.append({"id": f"pypact__{tag}", "group": "pypact", "path": path, "args": [], "validate_files": "recorded",
                      "validate": "recorded", "variant": "as_is", "pypact_deck": tag, "must_import": False})
        decks.append({"id": f"pypact__{tag}__synthetic_fluxes", "group": "pypact", "path": path, "args": None,
                      "validate_files": "recorded", "validate": "required", "variant": "synthetic_fluxes",
                      "pypact_deck": tag, "must_import": True})
    return decks


# ------------------------------------------------------------------------------ judging


class Context:
    def __init__(self, candidate, workdir, timeout, keep_all, fluxes):
        self.candidate, self.workdir, self.timeout, self.keep_all, self.fluxes = candidate, workdir, timeout, keep_all, fluxes
        env = dict(os.environ)
        for name in ("ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE"):
            env.pop(name, None)
        self.env = env


def load_json(path):
    try:
        return json.loads(Path(path).read_text()), None
    except (OSError, ValueError) as error:
        return None, f"{type(error).__name__}: {error}"


def check_report(report, model, failures):
    """The sidecar's shape, its counts, and every keyword occurrence of the deck. Returns the entries."""
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        failures.append(f"sidecar schema is not {REPORT_SCHEMA}")
        return []
    entries = report.get("entries")
    if not isinstance(entries, list):
        failures.append("sidecar has no entries list")
        return []
    counts = Counter()
    for index, entry in enumerate(entries):
        ok = (isinstance(entry, dict) and isinstance(entry.get("keyword"), str) and isinstance(entry.get("line"), int)
              and entry.get("phase") in PHASES and entry.get("status") in STATUSES
              and isinstance(entry.get("detail"), str) and isinstance(entry.get("args"), str))
        if not ok:
            failures.append(f"entry {index} is malformed: {str(entry)[:120]}")
            return entries
        counts[entry["status"]] += 1
    declared = report.get("counts") or {}
    if {s: counts.get(s, 0) for s in STATUSES} != {s: declared.get(s) for s in STATUSES}:
        failures.append(f"counts {declared} do not match the entries {dict(counts)}")
    if model is not None:
        have = defaultdict(list)
        for entry in entries:
            have[(entry["line"], entry["keyword"])].append(entry["phase"])
        need = Counter((line, keyword) for line, keyword, _ in model.occurrences)
        missing = []
        for (line, keyword), count in need.items():
            if len(have[(line, keyword)]) < count:
                missing.append(f"{keyword}@{line}")
        for line, keyword, phase in model.occurrences:
            phases = have.get((line, keyword))
            if phases and phase not in phases:
                failures.append(f"{keyword} at line {line}: phase {phases} but the deck puts it in {phase}")
                break
        if missing:
            failures.append(f"{len(missing)} keyword occurrence(s) without an entry: {', '.join(missing[:12])}")
    return entries


def judge_run(ctx, deck_path, out_path, extra, timeout, failures):
    """Run one import; returns (code, stdout, stderr, row fragment) and appends failures."""
    remove_outputs(out_path)
    started = time.monotonic()
    code, stdout, stderr = run_cmd([ctx.candidate, "import-fispact", deck_path, out_path] + extra, timeout, env=ctx.env)
    fragment = {"exit": code, "wall_s": round(time.monotonic() - started, 3)}
    if code is None:
        failures.append(f"timeout or launch failure: {stderr.decode(errors='replace')[:160]}")
        fragment["outcome"] = "timeout"
        return code, stdout, stderr, fragment
    signs = panic_signs(code, stdout, stderr)
    if signs:
        failures.append("crash or panic: " + "; ".join(signs))
        fragment["outcome"] = "panic"
        fragment["stderr_tail"] = tail(stderr, 300)
    return code, stdout, stderr, fragment


def judge_deck(ctx, d, data, deck_path, out_path, extra, timeout):
    """All checks for one deck. Returns the result row."""
    failures = []
    row = {"id": d["id"], "group": d["group"]}
    # the checker's own view of the deck
    model, model_error, undecodable = None, None, False
    try:
        text = data.decode("utf-8")
        try:
            model = parse_model(text)
        except ModelError as error:
            model_error = str(error)
    except UnicodeDecodeError:
        undecodable = True
    code, stdout, stderr, fragment = judge_run(ctx, deck_path, out_path, extra, timeout, failures)
    row.update(fragment)
    if fragment.get("outcome") in ("timeout", "panic"):
        row["failures"] = failures
        return row, failures, None
    row["message"] = first_line(stderr)
    spec_path, report_path = Path(out_path), Path(report_path_of(out_path))
    if code == 0:
        row["outcome"] = "imported"
        spec, spec_error = load_json(spec_path)
        report, report_error = load_json(report_path)
        if undecodable:
            failures.append("exit 0 on a deck that is not valid UTF-8")
        if spec_error:
            failures.append(f"spec not written or not JSON: {spec_error}")
        if report_error:
            failures.append(f"sidecar not written or not JSON: {report_error}")
        if model_error:
            failures.append(f"exit 0, but the importer's parser rules refuse the deck: {model_error}")
        entries = []
        if report is not None:
            entries = check_report(report, model, failures)
        counts = Counter(e["status"] for e in entries if isinstance(e, dict) and "status" in e)
        row["entries"] = len(entries)
        row["status_counts"] = {s: counts.get(s, 0) for s in STATUSES}
        row["required_additions"] = len((report or {}).get("required_additions") or [])
        if model is not None:
            row["occurrences"] = len(model.occurrences)
            if model.unreported:
                row["unreported_by_design"] = [f"{k}@{l} ({why})" for l, k, why in model.unreported[:10]]
        # --strict
        has_unsupported = counts.get("unsupported", 0) > 0
        strict_out = output_files(out_path)[2]
        remove_outputs(strict_out)
        scode, sout, serr = run_cmd([ctx.candidate, "import-fispact", deck_path, strict_out] + extra + ["--strict"], timeout,
                                    env=ctx.env)
        strict = {"exit": scode, "expected_failure": has_unsupported}
        signs = panic_signs(scode, sout, serr)
        if scode is None:
            failures.append("--strict run timed out or did not start")
        elif signs:
            failures.append("--strict run crashed: " + "; ".join(signs))
        elif has_unsupported:
            if scode == 0:
                failures.append("--strict accepted a deck whose sidecar has unsupported entries")
            elif scode not in ALLOWED_ERROR_CODES:
                failures.append(f"--strict exited {scode} on unsupported entries")
            elif "--strict" not in serr.decode(errors="replace"):
                failures.append("--strict refusal does not name --strict")
            if strict_out.exists():
                strict["wrote_spec_on_refusal"] = True
        else:
            if scode != 0:
                failures.append(f"--strict exited {scode} although nothing is unsupported: {first_line(serr)}")
            elif spec is not None:
                strict_spec, _ = load_json(strict_out)
                if strict_spec != spec:
                    failures.append("--strict produced a different spec")
        row["strict"] = strict
        row["unsupported"] = counts.get("unsupported", 0)
        if d.get("real") and spec is not None:
            check_validate(ctx, d, spec_path, row, failures)
    else:
        if code not in ALLOWED_ERROR_CODES:
            failures.append(f"exit code {code} (expected 0, 1 or 2)")
        if not stderr.strip():
            failures.append("non-zero exit with nothing on stderr")
        row["outcome"] = "error" if not failures else "bad_error"
        if undecodable and "utf-8" not in stderr.decode(errors="replace").lower():
            row["note"] = "invalid UTF-8 refused with a message that does not mention UTF-8"
        if d.get("must_import"):
            failures.append(f"deck refused: {first_line(stderr)}")
    if failures:
        row["outcome"] = "FAIL"
        row["failures"] = failures
        row["stderr_tail"] = tail(stderr, 300)
    return row, failures, code


def check_validate(ctx, d, spec_path, row, failures):
    result = {}
    code, out, err = run_cmd([ctx.candidate, "validate", spec_path], ctx.timeout, env=ctx.env)
    result["plain"] = {"exit": code, "message": first_line(err or out)}
    if code != 0:
        if d["validate"] == "required":
            failures.append(f"validate failed: {first_line(err or out)}")
    signs = panic_signs(code, out, err)
    if signs:
        failures.append("validate crashed: " + "; ".join(signs))
    code, out, err = run_cmd([ctx.candidate, "validate", spec_path, "--files"], ctx.timeout, env=ctx.env)
    result["files"] = {"exit": code, "message": first_line(err or out)}
    signs = panic_signs(code, out, err)
    if signs:
        failures.append("validate --files crashed: " + "; ".join(signs))
    elif code != 0 and d["validate_files"] == "required":
        failures.append(f"validate --files failed: {first_line(err or out)}")
    row["validate"] = result


# ------------------------------------------------------------------------------ driver


def output_files(out):
    """The files an import writes for OUT.json, and for its --strict twin."""
    out = Path(out)
    strict = out.with_name(out.stem + ".strict.json") if out.suffix == ".json" else out.with_name(out.name + ".strict.json")
    return [out, Path(report_path_of(out)), strict, Path(report_path_of(strict))]


def materialise_synthetic(decks, workdir, fluxes):
    """Write the synthetic decks and return the work items."""
    placeholders = {"{files_ok}": fluxes["files_ok"], "{files_nofluxes}": fluxes["files_nofluxes"],
                    "{files_missing_fluxes}": fluxes["files_missing_fluxes"], "{fluxdir}": workdir / "fluxes",
                    "{flux709}": fluxes["709"]}
    items = []
    for d in decks:
        directory = workdir / "decks" / d["category"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{d['id']}.i"
        path.write_bytes(d["data"])
        out_dir = workdir / "out" / d["category"]
        out_dir.mkdir(parents=True, exist_ok=True)
        extra = [str(placeholders.get(argument, argument)) for argument in d["args"]]
        if d["flux"] is not None:
            extra = ["--fluxes", str(fluxes[d["flux"]])] + extra
        item = dict(d, path=path, out=out_dir / f"{d['id']}.json", extra=extra, real=False, must_import=False)
        expect = d.get("expect")
        if expect == "deck_missing":
            item["path"] = directory / "no_such_deck.i"
        elif expect == "deck_directory":
            item["path"] = directory
        elif expect == "out_missing_dir":
            item["out"] = workdir / "out" / "no_such_directory" / "x.json"
        elif expect == "out_no_suffix":
            item["out"] = out_dir / f"{d['id']}.spec"
        elif expect == "no_args":
            item["no_args"] = True
        items.append(item)
    return items


def run_item(ctx, item, timeout):
    if item.get("no_args"):
        failures = []
        code, stdout, stderr = run_cmd([ctx.candidate, "import-fispact"], timeout, env=ctx.env)
        row = {"id": item["id"], "group": item["group"], "exit": code}
        signs = panic_signs(code, stdout, stderr)
        if code is None:
            failures.append("timeout")
        elif signs:
            failures.append("crash or panic: " + "; ".join(signs))
        elif code not in ALLOWED_ERROR_CODES or not stderr.strip():
            failures.append(f"exit {code} with stderr {first_line(stderr)!r}")
        row["outcome"] = "FAIL" if failures else "error"
        row["message"] = first_line(stderr)
        if failures:
            row["failures"] = failures
        return row, failures
    path = item["path"]
    try:
        data = path.read_bytes() if path.is_file() else b""
    except OSError:
        data = b""
    row, failures, _ = judge_deck(ctx, item, data, path, item["out"], item["extra"], timeout)
    return row, failures


def cleanup(ctx, item, failures):
    if failures or item.get("real") or ctx.keep_all:
        return
    for candidate in output_files(item["out"]):
        try:
            candidate.unlink()
        except (FileNotFoundError, IsADirectoryError):
            pass


def self_check(workdir):
    fluxes = write_flux_files(workdir / "fluxes")
    decks = build_synthetic(fluxes)
    problems = []
    # keyword table against the importer source
    if IMPORT_RS.is_file():
        source = IMPORT_RS.read_text()
        rust = re.findall(r'^    \("([A-Z0-9]+)", Arity::(\w+)\),$', source, re.M)
        if rust != KEYWORDS:
            only_rust = sorted(set(rust) - set(KEYWORDS))
            only_here = sorted(set(KEYWORDS) - set(rust))
            problems.append(f"keyword table differs from fispact_import.rs: only in source {only_rust}, only here {only_here}")
    else:
        problems.append(f"{IMPORT_RS} not found")
    if len({d['id'] for d in decks if d['category'] == 'keyword_abbrev'}) != sum(1 for d in decks if d['category'] == 'keyword_abbrev'):
        problems.append("duplicate abbreviation deck ids")
    ids = Counter((d["category"], d["id"]) for d in decks)
    problems.extend(f"duplicate deck id {k}" for k, n in ids.items() if n > 1)
    counts = Counter(d["category"] for d in decks)
    refused, parsed, undecodable = Counter(), Counter(), Counter()
    for d in decks:
        try:
            text = d["data"].decode("utf-8")
        except UnicodeDecodeError:
            undecodable[d["category"]] += 1
            continue
        try:
            model = parse_model(text)
            parsed[d["category"]] += 1
            if d["category"] == "keyword_valid" and not model.occurrences:
                problems.append(f"valid deck {d['id']} has no occurrences")
        except ModelError:
            refused[d["category"]] += 1
            if d["category"] == "keyword_valid":
                problems.append(f"valid deck {d['id']} is refused by the parser port")
        except Exception as error:  # a bug in this script
            problems.append(f"model failed on {d['id']}: {type(error).__name__}: {error}")
    materialise_synthetic(decks, workdir, fluxes)
    print(f"P140 H3 self-check: {len(KEYWORDS)} keywords, {len(decks)} synthetic decks written under {workdir / 'decks'}")
    for category in CATEGORIES:
        print(f"  {category:<20} {counts[category]:>5} decks  (parser port: {parsed[category]} parse, {refused[category]} refused, "
              f"{undecodable[category]} not UTF-8)")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    # the real decks, read as text only
    real = collect_real(DEFAULT_FNS, DEFAULT_FISSION, DEFAULT_PYPACT) if DEFAULT_FNS.is_dir() else []
    group_counts = Counter(r["group"] for r in real)
    bad_real = []
    for r in real:
        if r["group"] == "pypact" and r.get("variant") == "synthetic_fluxes":
            continue
        try:
            parse_model(r["path"].read_bytes().decode("utf-8"))
        except (ModelError, UnicodeDecodeError, OSError) as error:
            bad_real.append(f"{r['id']}: {error}")
    n_pypact = len({r['pypact_deck'] for r in real if r['group'] == 'pypact'})
    print(f"  real decks: fns {group_counts['fns']} (expected {EXPECTED['fns']}), fission {group_counts['fission']} "
          f"(expected {EXPECTED['fission']}), pypact {n_pypact} decks in {group_counts['pypact']} runs (expected {EXPECTED['pypact']})")
    for item in bad_real:
        print(f"PROBLEM: parser port refuses a real deck: {item}")
    problems.extend(bad_real)
    print("P140 H3 self-check: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
    return 0 if not problems else 1


def group_line(name, rows, expected=None, decks=None):
    n = len(rows) if decks is None else decks
    failed = sum(1 for r in rows if r["outcome"] in ("FAIL", "timeout", "panic"))
    imported = sum(1 for r in rows if r["outcome"] == "imported")
    refused = sum(1 for r in rows if r["outcome"] == "error")
    extra = f", expected {expected}" if expected is not None else ""
    runs = f" ({len(rows)} runs)" if decks is not None and decks != len(rows) else ""
    return (f"P140 H3 {name}: {n} decks{extra}{runs}; {imported} imported, {refused} refused with an error, "
            f"{failed} FAILED")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", help="release binary of the commit under test")
    ap.add_argument("--workdir", default="target/p140-import")
    ap.add_argument("--out", default="results/p140_import.json")
    ap.add_argument("--timeout", type=int, default=300, help="seconds allowed per command")
    ap.add_argument("--fns", default=str(DEFAULT_FNS))
    ap.add_argument("--fission", default=str(DEFAULT_FISSION))
    ap.add_argument("--pypact", default=str(DEFAULT_PYPACT))
    ap.add_argument("--keep-all", action="store_true", help="keep the outputs of passing synthetic decks")
    ap.add_argument("--only", help="debugging aid: run only the synthetic decks whose category/id matches this regex "
                                   "(a verdict from a partial run is not a gate result)")
    ap.add_argument("--self-check", action="store_true", help="generate the synthetic decks and count them; run nothing")
    args = ap.parse_args(argv)
    workdir = Path(args.workdir)
    workdir = workdir if workdir.is_absolute() else ROOT / workdir
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    workdir.mkdir(parents=True, exist_ok=True)
    if args.self_check:
        return self_check(workdir)
    if not args.candidate:
        print("--candidate is required", file=sys.stderr)
        return 2
    candidate = Path(args.candidate).resolve()
    fns, fission, pypact = (Path(os.path.expanduser(p)).resolve() for p in (args.fns, args.fission, args.pypact))
    missing = [f"{label}: {path}" for label, path in (("candidate binary", candidate), ("FNS decks", fns),
                                                      ("fission decks", fission), ("pypact", pypact / "reference"),
                                                      ("protocol", PROTOCOL)) if not Path(path).exists()]
    if missing:
        print("missing inputs:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 2

    fluxes = write_flux_files(workdir / "fluxes")
    ctx = Context(candidate, workdir, args.timeout, args.keep_all, fluxes)
    synthetic = build_synthetic(fluxes)
    record = {"protocol": "ACTINV-P140", "gate": "H3", "checker": record_file(Path(__file__).resolve()),
              "protocol_file": record_file(PROTOCOL), "candidate_binary": record_file(candidate),
              "timeout_s": args.timeout, "python": sys.version.split()[0],
              "pypact": {"commit": PYPACT_COMMIT, "license": PYPACT_LICENSE, "root": str(pypact)},
              "inputs": {"fns": str(fns), "fission": str(fission), "pypact": str(pypact)}}
    code, head, _ = run_cmd(["git", "-C", pypact, "rev-parse", "HEAD"], 60)
    record["pypact"]["head"] = head.decode().strip() if code == 0 else None
    if record["pypact"]["head"] not in (None, PYPACT_COMMIT):
        print(f"pypact checkout is at {record['pypact']['head']}, not {PYPACT_COMMIT}", file=sys.stderr)
        return 2
    record["synthetic_fluxes"] = {key: sha256_file(path) for key, path in sorted(fluxes.items())}
    digest = hashlib.sha256()
    for d in synthetic:
        digest.update(f"{d['category']}/{d['id']}\0".encode() + d["data"] + b"\0")
    record["synthetic_decks_sha256"] = digest.hexdigest()
    record["synthetic_decks"] = {c: sum(1 for d in synthetic if d["category"] == c) for c in CATEGORIES}

    rows_by_group = defaultdict(list)
    panics, error_messages = [], Counter()
    unreported = []

    def record_row(item, row, failures):
        rows_by_group[row["group"]].append(row)
        if row.get("outcome") == "panic" or any("crash or panic" in f or "crashed" in f for f in failures):
            panics.append({"id": row["id"], "group": row["group"], "exit": row.get("exit"), "stderr_tail": row.get("stderr_tail", "")})
        if row.get("outcome") in ("error", "bad_error") and row.get("message"):
            error_messages[normalise_message(row["message"])] += 1
        for entry in row.get("unreported_by_design", []):
            unreported.append(f"{row['id']}: {entry}")

    # ---- real decks
    real = collect_real(fns, fission, pypact)
    real_dir = workdir / "out" / "real"
    real_dir.mkdir(parents=True, exist_ok=True)
    record["input_hashes"] = {}
    for d in real:
        d = dict(d, real=True)
        if d["args"] is None:
            model_projectile = None
            try:
                model_projectile = parse_model(d["path"].read_text()).projectile
            except (ModelError, OSError, UnicodeDecodeError):
                pass
            key = "162" if model_projectile in (2, 3, 4, 5, 6, 7) else "709"
            d["args"] = ["--fluxes", str(fluxes[key])]
        out = real_dir / f"{d['id']}.json"
        item = dict(d, out=out, extra=d["args"])
        record["input_hashes"][d["id"]] = sha256_file(d["path"])
        row, failures = run_item(ctx, item, args.timeout)
        row["deck"] = str(d["path"])
        if d["group"] == "pypact":
            row["variant"] = d["variant"]
        record_row(item, row, failures)
        if failures:
            print(f"  FAIL {d['group']} {d['id']}: {'; '.join(failures)[:200]}")

    # ---- synthetic decks
    items = materialise_synthetic(synthetic, workdir, fluxes)
    if args.only:
        pattern = re.compile(args.only)
        items = [i for i in items if pattern.search(f"{i['category']}/{i['id']}")]
        record["partial_run"] = {"only": args.only, "synthetic_decks_run": len(items)}
    for number, item in enumerate(items, start=1):
        timeout = min(args.timeout, item["timeout"]) if item.get("timeout") else args.timeout
        row, failures = run_item(ctx, item, timeout)
        record_row(item, row, failures)
        if failures:
            print(f"  FAIL {item['group']} {item['id']}: {'; '.join(failures)[:200]}")
        cleanup(ctx, item, failures)
        if number % 500 == 0:
            print(f"  ... {number}/{len(items)} synthetic decks")

    # ---- verdict
    groups = {}
    lines = []
    real_counts = {"fns": len(rows_by_group["fns"]), "fission": len(rows_by_group["fission"]),
                   "pypact": len({r['id'].split('__synthetic_fluxes')[0] for r in rows_by_group["pypact"]})}
    for name in ("fns", "fission", "pypact"):
        rows = rows_by_group[name]
        failed = [r for r in rows if r["outcome"] in ("FAIL", "timeout", "panic")]
        size_ok = real_counts[name] == EXPECTED[name]
        groups[name] = {"decks": real_counts[name], "expected": EXPECTED[name], "runs": len(rows), "failed": len(failed),
                        "size_ok": size_ok, "pass": bool(size_ok and not failed)}
        lines.append(group_line(name, rows, EXPECTED[name], real_counts[name]) + ("" if size_ok else "  [WRONG DECK COUNT]"))
    for category in CATEGORIES:
        rows = rows_by_group[f"synthetic/{category}"]
        failed = [r for r in rows if r["outcome"] in ("FAIL", "timeout", "panic")]
        groups[f"synthetic/{category}"] = {"decks": len(rows), "failed": len(failed), "pass": not failed}
        lines.append(group_line(f"synthetic/{category}", rows))
    all_rows = [r for rows in rows_by_group.values() for r in rows]
    failing = [r for r in all_rows if r["outcome"] in ("FAIL", "timeout", "panic")]
    strict_checked = sum(1 for r in all_rows if "strict" in r)
    strict_refused = sum(1 for r in all_rows if r.get("strict", {}).get("expected_failure"))
    status_totals = Counter()
    for r in all_rows:
        status_totals.update(r.get("status_counts", {}))
    overall = bool(all(g["pass"] for g in groups.values()) and not panics and not args.only)
    record.update({
        "groups": groups,
        "counts": {"decks": len(all_rows), "failed": len(failing), "panics": len(panics), "imported": sum(1 for r in all_rows if r["outcome"] == "imported"),
                   "refused_with_error": sum(1 for r in all_rows if r["outcome"] == "error"),
                   "strict_checked": strict_checked, "strict_expected_refusals": strict_refused,
                   "entry_status_totals": {s: status_totals.get(s, 0) for s in STATUSES}},
        "panics": panics,
        "failures": [{"id": r["id"], "group": r["group"], "failures": r.get("failures", [])} for r in failing[:400]],
        "error_messages": dict(error_messages.most_common(80)),
        "unreported_by_design": unreported[:200],
        "unreported_by_design_total": len(unreported),
        "decks": {name: rows for name, rows in sorted(rows_by_group.items())},
        "verdict": "P140-H3-PASS" if overall else "P140-H3-FAIL",
    })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for line in lines:
        print(line)
    print(f"P140 H3 pypact: {PYPACT_LICENSE}, commit {PYPACT_COMMIT[:8]}; panics {len(panics)}; "
          f"strict checked on {strict_checked} imported decks ({strict_refused} refused as expected)")
    print(f"P140 H3 verdict: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
