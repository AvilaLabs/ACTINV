#!/usr/bin/env python3
"""P155 checker (protocols/ACTINV-P155_PROTOCOL.md): the FISPACT-II importer sweep. Gates G0 and G2-G7 are evaluated
here; G1 (fmt, clippy, tests) and G8 (CI, committed baselines) are recorded as external.

Independence: written from the protocol, the documented importer CLI and report format (docs/guide/fispact-migration.md,
`actinv-fispact-import-report-1`), the spec field names of master and of the P152 and P154 specs, the user documentation
of the damage table, and the FISPACT-II manual as paraphrased in docs/maintainers/FISPACT_MANUAL_KEYWORD_AUDIT.md. No
implementation of the new importer features was read. Where the protocol leaves a detail open (report wording, the
spelling of a list option, the order of a parent list) the checks look for the status, the option name or the keyword
named in the detail, never an exact string; see ASSUMPTIONS below.

  G0  This protocol's SHA-256 is in protocols/protocol_hash.txt.
  G1  External (fmt, clippy, workspace tests): recorded, not run.
  G2  Defaults unchanged. The 132 FNS decks import with the reference and the candidate to byte-identical specs and
      identical report statuses. The pypact (5 reference decks + examples/files/test.i) and CONDERC fission decks,
      imported without any new option (a synthetic fluxes file where the deck irradiates), differ only in spec fields
      and report entries set by keywords of items 2-7 that need no option; every difference is listed with its keyword.
  G3  Keyword mapping. One synthetic deck per keyword of items 2-7 (generated here), imported by the candidate, gives
      the spec fragment and status of the protocol: ALLDISPEN / ATDISPEN in both orders, FISYIELD with positive,
      negative and zero counts, UNCTYPE 1/2/3, SENSITIVITY SIGMA with MCSAMPLE / MCSEED / ERROR, SSFGEOMETRY shapes,
      PROBTABLE, SSFDILUTION (group index converted), the corrections of item 7 and the keywords item 7 keeps.
  G4  Imported specs run. Five cases (fission with CUMFYLD + FISYIELD, fission with the default parent set, SPEK + NOXRAY,
      READGG, damage): the importer's spec runs (`actinv run`) and its result equals, apart from `ms`, the result of
      a spec the checker writes itself (the imported spec with the feature blocks replaced by blocks built here from the
      checker's own hashes); every supplied file's sha256 in the spec equals the checker's hash.
  G5  Corpus. The two CONDERC fission decks with --fission-yields import with no unsupported entry for a keyword of
      items 2-7 and none beyond the reference's own, and their specs run. The pypact decks (with a ggbins files file)
      import with no unsupported entry except the keywords item 7 keeps.
  G6  Refusals. Every missing-option case, a damage table with a mismatched displacement energy (by deck value and,
      with --damage-table-mismatch, by a second table), unparsable ggbins files, SENSITIVITY LAMBDA and an orphan
      MCSAMPLE are `unsupported` with a reason naming the cause, and --strict exits nonzero and writes no spec.
  G7  The scorecard and the handbook import page list the new options and the keywords; check_p140_docs.py passes.
  G8  External (CI on the pushed commit, committed baselines): recorded, not run.

ASSUMPTIONS (open points of the protocol, recorded in the JSON as well):
  A1  A list option takes its files after one flag (`--fission-yields A B`); --file-list-style repeat repeats the flag.
  A2  FISPACT-II group numbers run from the highest energy (the order of the fluxes file), so the 1-based group n of a
      library with N groups is the 0-based ascending-energy index N - n of `self_shielding.dilution_overrides`.
  A3  UNCTYPE 1 may leave `uncertainty.channels` absent or empty (the engine default is the same channel).
  A4  An options.outputs list is extended with `photons` only when a list exists and lacks it; an absent list already
      produces photons.
  A5  FISCHOOSE reaches `pathways.fission_parents` when a pathways section exists (the decks carry PATH).
  A6  Resonance files and the Bondarenko table of the self-shielding cases are placeholders written under the work
      directory (no .endf/.npz/.zip names); those cases check the import mapping only and are never run.
  A7  Where a spec writes a nuclide, any spelling that names the same (symbol, A, isomer) passes ("U235", "U-235").
  A8  The expected shape and lengths of SSFGEOMETRY are those of the manual: 1 foil (thickness), 2 wire (radius),
      3 sphere (radius), 4 cylinder (radius, height) -> shape, length_cm, height_cm; the density is the DENSITY value
      in `geometry.density_g_cm3` or, when absent there, `material.density_g_cm3`.
  A9  A report detail "reports" a fact when the (lowercased) detail of the keyword's entry contains the fact's word.

Usage:
  check_p155.py --reference REF_BIN --candidate CAND_BIN --damage-table TABLE2.json [--damage-table-mismatch T2.json]
      [--damage-table-sha256 HEX] [--workdir target/p155-work] [--out results/p155_importer_sweep.json]
      [--timeout 3600] [--import-timeout 300] [--fns DIR] [--pypact DIR] [--conderc DIR] [--nfpy-dir DIR]
      [--data-dir actinv-data/v1.1.0] [--file-list-style space|repeat] [--skip-docs] [--reuse]
  check_p155.py --self-check          offline unit tests of the helpers (no binary, no data, no subprocess)
  check_p155.py --list-expectations   print the expected-fragment table of G3 and G6 (no binary)

Run inside the required systemd cgroup. Exit 0 on PASS, 1 on FAIL, 2 on a missing or mismatching pinned input.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P155_PROTOCOL.md"
AMENDMENT = ROOT / "protocols" / "ACTINV-P155_AMENDMENT_A.md"
AMENDMENT_B = ROOT / "protocols" / "ACTINV-P155_AMENDMENT_B.md"
HASH_LOG = ROOT / "protocols" / "protocol_hash.txt"
SCORECARD = ROOT / "COMPETITIVE_BENCHMARK.md"
HANDBOOK = ROOT / "docs" / "guide" / "fispact-migration.md"
DOCS_CHECKER = HERE / "check_p140_docs.py"

REFERENCE_SHA256 = "79e1e8600a8c2ad3ce42e72dcf34ce4dd8fa9bf60288db24e9eeb077781c9072"
REPORT_SCHEMA = "actinv-fispact-import-report-1"
STATUSES = ("mapped", "approximated", "noted", "unsupported")
LIBRARY_NAME = "tendl-2025-neutron-709g.npz"
LIBRARY_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"   # examples/fns_fe_5min.json
N_GROUPS = 709
ABSENT = "<absent>"
MAX_LISTED = 40
EXPECTED_FNS_DECKS = 132

# ------------------------------------------------------------------------------ pinned inputs (sha256sum, 2026-10-27)

YIELD_PINS = {
    "nfy-092_U_235.endf": "9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41",
    "nfy-092_U_238.endf": "05598b1c16117a729a3314842a2de574ffb7c992f679f6e1505c1d566164791c",
    "nfy-094_Pu_239.endf": "a2713aa6d0283b879040a07fa29d3e99b2d660b619c6d11a2a71aa9d5c5a2605",
    "nfy-090_Th_232.endf": "551d2721427034042ed31307b297011b0b2a092b7baeeac6a5d3a6d90472d052",
}
PYPACT_PINS = {
    "reference/test.i": "3975833023a37ec112d40a4f8d94c7901fa3373d0da585f20e733d8ecb0488ac",
    "reference/test2.i": "e2eb423543a9e7cbb999acbd1025fe11318f2c7ae4b85214b54edc269d2c9ae6",
    "reference/test3.i": "a543d0e1950f4072add545b818e56434e8011202ad690c5958f9cd22222437b2",
    "reference/test4.i": "5f4f73522c8b48002d9762a58fb38321d95542ecb57f0481e2e2bda3d27fd972",
    "reference/test5.i": "47298a2a81b412c3fe015502168388b148caa1d3f0141867ee7958104a404084",
    "examples/files/test.i": "4435fb6e10c9222984101c4291753244b90cdaf0fee24b56df4d3d1e33753d5d",
}
CONDERC_PINS = {
    "U2352E4s.i": "a4d0a80a7f59f16db6ae15119d77468a45facc02d01f4fdaa96036047e372b94",
    "U235pulse.i": "ecba520a89bd8a1046088c40ba300016355220b552e8ca6ae8451f84161aa477",
    "fluxes_therm": "46c42e3ea73803bc791c55209d8bb8aeceffaf9e2bbce90aa645ff02acb4f68f",
}
# manifest digests of the FNS corpus: sha256 over "relative/path\0sha256(file)\n" for the sorted decks (*.i), and for
# decks + files files (*_files) + fluxes files (*_fluxes)
FNS_DECKS_DIGEST = "ee710b72a918a23b2f66373de0280d9520c30e6634db24011293d9ddf4ee1d2a"
FNS_INPUTS_DIGEST = "5b477313e2a013db36a331ae047d4279454c1aad8f9447398b6313b44a3e0e31"

# ------------------------------------------------------------------------------ keyword sets

# keywords of items 2-7 plus the keywords item 7 keeps; the only ones a G2 difference may be attributed to
ITEM_KEYWORDS = frozenset({
    "USEFISSION", "NOFISS", "CUMFYLD", "FISYIELD", "FISCHOOSE", "NOXRAY", "SPEK", "READGG", "ALLDISPEN", "ATDISPEN",
    "UNCTYPE", "COVARIANCE", "SENSITIVITY", "MCSAMPLE", "MCSEED", "ERROR", "SSFGEOMETRY", "SSFCHOOSE", "SSFMASS",
    "SSFFUEL", "PROBTABLE", "SSFDILUTION", "XSTHRESHOLD", "DEPLETION", "RESULT", "IRON", "READSF", "USESPALLATION",
    "USEXSEXTRA", "GETXS", "GETDECAY", "ADCROSS", "ADLAM",
})
# item 7: stay unsupported (G5 pypact allows exactly these)
RETAINED_UNSUPPORTED = frozenset({"READSF", "USESPALLATION", "USEXSEXTRA", "GETXS", "GETDECAY", "ADCROSS", "ADLAM"})
# spec path prefix -> the keywords that may set it without a supplied option; checked longest-first, in this order.
# fission_yields.files, damage and self_shielding have no entry on purpose: they need an option (or a file) the
# option-free G2 imports do not supply.
SPEC_OWNERS = (
    ("fission_yields.files", frozenset()),
    ("fission_yields", frozenset({"CUMFYLD", "FISYIELD", "NOFISS"})),
    ("pathways.fission_parents", frozenset({"FISCHOOSE"})),
    ("photon.missing_spectra", frozenset({"SPEK"})),
    ("photon.exclude_xrays", frozenset({"NOXRAY"})),
    ("photon.group_boundaries_eV", frozenset({"READGG"})),
    ("options.outputs", frozenset({"SPEK", "NOXRAY"})),
    ("uncertainty.channels", frozenset({"UNCTYPE"})),
    ("uncertainty.voi", frozenset({"SENSITIVITY"})),
    ("overrides", frozenset({"ERROR"})),
)
DEFAULT_FISSION_PARENTS = (("U", 235, 0), ("U", 238, 0), ("PU", 239, 0))
OLD_IRON_WORDS = ("fictitious", "background removal", "iron background")


class MissingInput(Exception):
    """A pinned input is absent or has a different hash: the run stops with exit 2."""


# ------------------------------------------------------------------------------ small helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True)


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def report_path_of(out):
    out = str(out)
    return out[:-5] + ".import.json" if out.endswith(".json") else out + ".import.json"


def require_pinned(path, digest, label):
    path = Path(path)
    if not path.is_file():
        raise MissingInput(f"{label}: {path} is absent")
    actual = sha256_file(path)
    if actual != digest:
        raise MissingInput(f"{label}: {path} has sha256 {actual}, not the pinned {digest}")
    return {"path": str(path), "sha256": actual}


def manifest_digest(root, paths):
    """sha256 over 'relative/path NUL sha256(file) LF' for the paths sorted by their posix relative name."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda q: Path(q).relative_to(root).as_posix()):
        h.update(Path(p).relative_to(root).as_posix().encode() + b"\0" + sha256_file(p).encode() + b"\n")
    return h.hexdigest()


def fns_files(root):
    root = Path(root)
    decks = sorted(root.glob("*/*.i"))
    inputs = sorted(p for p in root.glob("*/*")
                    if p.is_file() and (p.suffix == ".i" or p.name.endswith("_files") or p.name.endswith("_fluxes")))
    return decks, inputs


def getpath(obj, dotted):
    """(found, value) of a dotted path of dict keys."""
    node = obj
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    return True, node


def same(a, b, rel=1e-12):
    """Equality with a relative tolerance on numbers, exact elsewhere."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b or abs(a - b) <= rel * max(abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(same(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y, rel) for x, y in zip(a, b))
    return a == b


def empty(value):
    return value is None or value == [] or value == {}


def nuclide_key(text):
    """(SYMBOL, A, isomer) of 'U235', 'U-235', 'Am242m', 'Am242m1', 'AM242M2'; None when it is not a nuclide."""
    m = re.fullmatch(r"([A-Za-z]{1,2})-?(\d{1,3})(?:[mM](\d*))?", str(text).strip())
    if not m:
        return None
    iso = 0 if m.group(3) is None else (int(m.group(3)) if m.group(3) else 1)
    return (m.group(1).upper(), int(m.group(2)), iso)


def yield_file_parent(name):
    m = re.fullmatch(r"nfy-\d+_([A-Za-z]{1,2})_(\d+)(?:m(\d*))?\.endf", name)
    if not m:
        raise ValueError(f"not an ENDF/B-VIII.0 yield file name: {name}")
    return (m.group(1).upper(), int(m.group(2)), 0 if m.group(3) is None else (int(m.group(3)) if m.group(3) else 1))


def normalize_result(obj, top=True):
    """Copy without the wall-clock keys: the top-level `ms` and, anywhere, `wall_time_s` and `cells_per_s`."""
    if isinstance(obj, dict):
        return {k: normalize_result(v, False) for k, v in obj.items()
                if not ((top and k == "ms") or k in ("wall_time_s", "cells_per_s"))}
    if isinstance(obj, list):
        return [normalize_result(v, False) for v in obj]
    return obj


def json_diffs(a, b, path="", out=None, limit=5000):
    """Differing paths of two JSON values as {path, ref, cand}; a key on one side only is reported at its leaves when
    its value is a non-empty object, else at the key."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else str(k)
            if k not in a:
                if isinstance(b[k], dict) and b[k]:
                    json_diffs({}, b[k], sub, out, limit)
                else:
                    out.append({"path": sub, "ref": ABSENT, "cand": b[k]})
            elif k not in b:
                if isinstance(a[k], dict) and a[k]:
                    json_diffs(a[k], {}, sub, out, limit)
                else:
                    out.append({"path": sub, "ref": a[k], "cand": ABSENT})
            else:
                json_diffs(a[k], b[k], sub, out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append({"path": path, "ref": f"list of {len(a)}", "cand": f"list of {len(b)}"})
        for i, (x, y) in enumerate(zip(a, b)):
            json_diffs(x, y, f"{path}[{i}]", out, limit)
    elif a != b or type(a) is not type(b):
        out.append({"path": path, "ref": a, "cand": b})
    return out


def path_under(path, prefix):
    return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


def spec_owners(path):
    """Keywords that may set the spec path without a supplied option; None when no rule covers it."""
    for prefix, owners in SPEC_OWNERS:
        if path_under(path, prefix):
            return owners
    return None


def entries_of(report, keyword=None, phase=None, status=None):
    if not isinstance(report, dict):
        return []
    out = []
    for e in report.get("entries") or []:
        if keyword is not None and str(e.get("keyword", "")).upper() != keyword.upper():
            continue
        if phase is not None and e.get("phase") != phase:
            continue
        if status is not None and e.get("status") != status:
            continue
        out.append(e)
    return out


def unsupported_keywords(report):
    return sorted({e["keyword"] for e in entries_of(report, status="unsupported")})


def status_rows(report):
    return [(e["keyword"], e["line"], e["status"]) for e in entries_of(report)]


def tally(report):
    counts = {s: 0 for s in STATUSES}
    for e in entries_of(report):
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return counts


def detail_text(report, keyword, status=None):
    return " | ".join(str(e.get("detail", "")) for e in entries_of(report, keyword, status=status)).lower()


def multi(flag, paths, style):
    """The command-line form of a list option (assumption A1)."""
    if style == "repeat":
        out = []
        for p in paths:
            out += [flag, str(p)]
        return out
    return [flag] + [str(p) for p in paths]


def dilution_index(fispact_group, n_groups=N_GROUPS):
    """Assumption A2: FISPACT-II group n (1-based, highest energy first) -> 0-based ascending-energy index."""
    return n_groups - fispact_group


# ------------------------------------------------------------------------------ process handling


def run_cmd(argv, timeout, cwd, env=None):
    """Run without a shell in its own session. Returns (exit code or None on timeout/OS error, stdout, stderr)."""
    try:
        proc = subprocess.Popen([str(a) for a in argv], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
    except OSError as error:
        return None, b"", f"{type(error).__name__}: {error}".encode()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate()
        return None, b"", f"timed out after {timeout} s".encode()
    return proc.returncode, out, err


def clean_env():
    env = dict(os.environ)
    for name in ("ACTINV_LIBRARY", "ACTINV_ENDF_DECAY", "ACTINV_COVARIANCE"):
        env.pop(name, None)
    return env


class Runner:
    """Calls to the importer and to `run`, serial, with the repository root as the working directory."""

    def __init__(self, work, timeout, import_timeout, reuse, cwd):
        self.work, self.timeout, self.import_timeout, self.reuse, self.cwd = work, timeout, import_timeout, reuse, cwd
        self.env = clean_env()
        self.runs = {}

    def import_deck(self, binary, deck, out, extra=(), strict=False):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        for p in (out, Path(report_path_of(out))):
            if p.exists():
                p.unlink()
        argv = [binary, "import-fispact", deck, out] + list(extra) + (["--strict"] if strict else [])
        code, so, se = run_cmd(argv, self.import_timeout, self.cwd, self.env)
        res = {"exit": code, "stdout": so.decode(errors="replace"), "stderr": se.decode(errors="replace"),
               "spec": None, "spec_bytes": None, "report": None, "out": str(out), "argv": [str(a) for a in argv]}
        if out.is_file():
            res["spec_bytes"] = out.read_bytes()
            try:
                res["spec"] = json.loads(res["spec_bytes"])
            except ValueError:
                res["spec"] = None
        rp = Path(report_path_of(out))
        if rp.is_file():
            try:
                res["report"] = json.loads(rp.read_text())
            except ValueError:
                res["report"] = None
        return res

    def run_spec(self, label, binary, spec):
        """`binary run SPEC OUT`; `spec` is a dict (written under the work directory) or a path. Returns the result."""
        (self.work / "runs").mkdir(parents=True, exist_ok=True)
        spec_path = self.work / "runs" / f"{label}.spec.json"
        if isinstance(spec, dict):
            write_text(spec_path, json.dumps(spec, indent=1) + "\n")
        else:
            spec_path = Path(spec)
        out_path, log_path = self.work / "runs" / f"{label}.json", self.work / "runs" / f"{label}.log"
        info = {"binary": str(binary), "spec": str(spec_path), "spec_sha256": sha256_file(spec_path)}
        self.runs[label] = info
        if not (self.reuse and out_path.exists()):
            if out_path.exists():
                out_path.unlink()
            with open(log_path, "wb") as log:
                try:
                    proc = subprocess.Popen([str(binary), "run", str(spec_path), str(out_path)], cwd=str(self.cwd),
                                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                except OSError as error:
                    info["error"] = f"{type(error).__name__}: {error}"
                    return None
                try:
                    code = proc.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        proc.kill()
                    proc.wait()
                    info["error"] = f"timed out after {self.timeout} s"
                    return None
            info["exit_code"] = code
            if code != 0:
                info["error"] = f"exit code {code}: " + tail(log_path)
                return None
        info["output_sha256"] = sha256_file(out_path)
        return json.loads(out_path.read_text())


def tail(path, size=400):
    try:
        return Path(path).read_text(errors="replace")[-size:].strip()
    except OSError:
        return ""


# ------------------------------------------------------------------------------ synthetic decks and files


def make_deck(title, library=(), library_late=(), material=None, initial=(), schedule=None):
    """A FISPACT-II deck of the structure the importer reads: library phase, FISPACT, initial phase, inventory phase.
    `library` goes before GETXS, `library_late` after GETDECAY (where the manual puts ALLDISPEN / ATDISPEN)."""
    material = list(material) if material is not None else ["DENSITY 7.874", "MASS 1.0E-3 1", " FE 100.0"]
    schedule = list(schedule) if schedule is not None else [
        "FLUX 1.0E+12", "TIME 1 HOURS ATOMS", "FLUX 0.", "ZERO", "TIME 1 DAYS ATOMS"]
    lines = ["CLOBBER"] + list(library) + ["GETXS 1 709", "GETDECAY 1"] + list(library_late) + [
        "FISPACT", f"* {title}"] + material + ["MIND 1.0"] + list(initial) + schedule + ["END", "* end"]
    return "\n".join(lines) + "\n"


FUEL_MATERIAL = ["DENSITY 19.0", "FUEL 2", " U235 1.0E+20", " U238 1.0E+20"]
FUEL_U235 = ["DENSITY 19.0", "FUEL 1", " U235 1.0E+20"]
TUNGSTEN = ["DENSITY 19.3", "MASS 1.0E-3 1", " W 100.0"]


def flux_file_text(groups, value=1.0e3):
    """A FISPACT-II fluxes file: `groups` equal values (highest energy first, 6 per line), the first-wall loading, a title."""
    lines = []
    for start in range(0, groups, 6):
        lines.append(" " + "  ".join(f"{value:.4E}" for _ in range(min(6, groups - start))))
    return "\n".join(lines + [" 1.000", "Synthetic flat spectrum for the P155 importer checks"]) + "\n"


def ggbins_text(bounds):
    return "\n".join([str(len(bounds) - 1)] + [f"{b:.6E}" for b in bounds]) + "\n"


GGBINS_GOOD = [1.0e3, 1.0e4, 1.0e5, 3.0e5, 6.0e5, 1.0e6, 2.0e6, 4.0e6, 1.0e7]
GGBINS_BAD = {
    "junk": "this is not a group file\n",
    "count_mismatch": "5\n1.0\n1.0E+05\n5.0E+05\n",
    "not_ascending": "3\n1.0E+06\n1.0E+05\n1.0E+03\n1.0E+02\n",
    "zero_first_energy": "3\n0.0\n1.0E+05\n1.0E+06\n1.0E+07\n",
    "empty": "",
}


def shield_table_json(bounds, nuclides, fispact_groups):
    """A minimal `actinv-shield-table-1` document (placeholder for the self-shielding import cases, never run): each
    nuclide gets a group entry at both conventions' index of every FISPACT group number given."""
    n = len(bounds) - 1
    rows = [[1.0] * 4 for _ in range(6)]
    factors = {"total": rows, "elastic": rows, "fission": rows, "capture": rows}
    out = {}
    for name, (za, groups) in nuclides.items():
        idx = sorted({i for g in groups for i in (dilution_index(g, n), g - 1) if 0 <= i < n})
        out[name] = {"za": za, "liso": 0, "unresolved_ranges_ev": [[bounds[0], bounds[-1]]], "nodes": [],
                     "groups": [{"group": i, "overlap_fraction": 0.5, "sigma_p_b": 6.0,
                                 "infinite_dilution_b": [10.0, 9.0, 0.0, 1.0], "factors": factors, "shielded_b": {}}
                                for i in idx]}
    return json.dumps({"format": "actinv-shield-table-1", "generator": "check_p155 placeholder",
                       "source": {"citation": "placeholder for P155 import checks", "edition": "none", "url": "none"},
                       "method": {}, "sigma0_b": [1e10, 1e3, 1e2, 1e1, 1.0, 0.1],
                       "temperatures_K": [293.6, 600.0, 900.0, 1200.0],
                       "group_structure": {"name": "placeholder-709", "boundaries_eV": bounds},
                       "files": [], "uncovered": [], "nuclides": out}, indent=1) + "\n"


def geometric_bounds(n=N_GROUPS, lo=1.0e-5, hi=1.0e9):
    ratio = (hi / lo) ** (1.0 / n)
    return [lo * ratio ** i for i in range(n + 1)]


class Fixtures:
    """Files the cases use, written under the work directory. Everything here is generated; nothing is tracked."""

    def __init__(self, work, yields, damage, style):
        self.work, self.yields, self.damage, self.style = Path(work), dict(yields), damage, style
        d = self.work / "fixtures"
        self.flux709 = write_text(d / "fluxes_709", flux_file_text(709))
        self.flux162 = write_text(d / "fluxes_162", flux_file_text(162))
        self.ggbins = write_text(d / "ggbins_good", ggbins_text(GGBINS_GOOD))
        self.files_ggbins = write_text(d / "files_ggbins", f"# P155 synthetic files file\nggbins {self.ggbins}\n")
        self.bad = {}
        for name, text in GGBINS_BAD.items():
            g = write_text(d / f"ggbins_{name}", text)
            self.bad[name] = write_text(d / f"files_ggbins_{name}", f"ggbins {g}\n")
        self.bad["missing_entry"] = write_text(d / "files_no_ggbins", "# no ggbins entry\nxs_endf /nonexistent/gxs-709\n")
        self.bad["nonexistent_file"] = write_text(d / "files_ggbins_nonexistent", "ggbins /nonexistent/p155/ggbins\n")
        self.table = write_text(d / "bondarenko_placeholder.json", shield_table_json(
            geometric_bounds(), {"W182": (74182, (300, 301)), "W184": (74184, (194, 200))}, (300, 301, 194, 200)))
        self.resonance = [write_text(d / f"resonance_placeholder_{name}.dat",
                                     f"placeholder resonance evaluation for {name}; P155 import checks only\n")
                          for name in ("W182", "W184")]

    def yield_paths(self, *names):
        return [self.yields[n]["path"] for n in names]

    def yield_args(self, *names):
        return multi("--fission-yields", self.yield_paths(*names), self.style)

    def damage_args(self):
        return ["--damage", self.damage["path"]]

    def table_args(self):
        return ["--self-shielding-table", str(self.table)]

    def resonance_args(self):
        return multi("--resonance", self.resonance, self.style)


# ------------------------------------------------------------------------------ checks


class Check:
    def __init__(self, desc, fn):
        self.desc, self.fn = desc, fn


def C_exit0():
    return Check("import exits 0 and writes spec and report",
                 lambda imp, base: (imp["exit"] == 0 and imp["spec"] is not None and imp["report"] is not None,
                                    f"exit {imp['exit']}: {imp['stderr'].strip()[-160:]}"))


def C_status(keyword, allowed, phase=None):
    allowed = tuple(allowed)

    def fn(imp, base):
        got = [e["status"] for e in entries_of(imp["report"], keyword, phase)]
        return bool(got) and all(s in allowed for s in got), f"{keyword} statuses {got}"
    return Check(f"{keyword} entries all {'/'.join(allowed)}" + (f" ({phase} phase)" if phase else ""), fn)


def C_detail(keyword, terms, mode="all", status=None):
    terms = [t.lower() for t in terms]

    def fn(imp, base):
        text = detail_text(imp["report"], keyword, status)
        hits = [t for t in terms if t in text]
        ok = bool(text) and (len(hits) == len(terms) if mode == "all" else bool(hits))
        return ok, f"{keyword} detail {text[:200]!r}"
    return Check(f"{keyword} detail names {'all' if mode == 'all' else 'one'} of {terms}", fn)


def C_eq(path, value):
    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, path)
        return found and same(got, value), f"{path} = {got!r}" if found else f"{path} absent"
    return Check(f"spec {path} == {value!r}", fn)


def C_absent(path):
    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, path)
        return (not found) or empty(got), f"{path} = {got!r}" if found else f"{path} absent"
    return Check(f"spec {path} absent or empty", fn)


def C_present(path):
    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, path)
        return found and not empty(got), f"{path} = {str(got)[:120]}" if found else f"{path} absent"
    return Check(f"spec {path} present", fn)


def C_channels(expected):
    """uncertainty.channels as a set (assumption A3: for {cross_section_mf33} absent or empty is the same channel)."""
    expected = set(expected)

    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, "uncertainty.channels")
        if not found or empty(got):
            return expected == {"cross_section_mf33"} and getpath(imp["spec"] or {}, "uncertainty")[0], \
                f"uncertainty.channels {'absent' if not found else got!r}"
        return isinstance(got, list) and set(got) == expected and len(got) == len(set(got)), f"channels {got!r}"
    return Check(f"spec uncertainty.channels == {sorted(expected)}", fn)


def C_names(path, expected):
    """A list of nuclide names at `path` equal, as (symbol, A, isomer) sets, to `expected` (assumption A7)."""
    expected = set(expected)

    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, path)
        if not found or not isinstance(got, list):
            return False, f"{path} absent or not a list"
        keys = [nuclide_key(x) for x in got]
        return None not in keys and set(keys) == expected and len(keys) == len(set(keys)), f"{path} = {got!r}"
    return Check(f"spec {path} names {sorted(expected)}", fn)


def C_parents(kind, expected):
    """fission_yields.parents has exactly the one key `kind` ('include' / 'exclude') naming `expected`."""
    expected = set(expected)

    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, "fission_yields.parents")
        if not found or not isinstance(got, dict) or set(got) != {kind} or not isinstance(got[kind], list):
            return False, f"fission_yields.parents = {got!r}" if found else "fission_yields.parents absent"
        keys = [nuclide_key(x) for x in got[kind]]
        return None not in keys and set(keys) == expected and len(keys) == len(set(keys)), f"parents {got!r}"
    return Check(f"spec fission_yields.parents.{kind} names {sorted(expected)}", fn)


def C_files(path, files):
    """`path` is a list of {path, sha256} equal (by real path and hash, any order) to the (path, sha) pairs given."""
    want = sorted((os.path.realpath(p), s) for p, s in files)

    def fn(imp, base):
        found, got = getpath(imp["spec"] or {}, path)
        if not found or not isinstance(got, list):
            return False, f"{path} absent"
        try:
            have = sorted((os.path.realpath(e["path"]), e["sha256"]) for e in got)
        except (KeyError, TypeError):
            return False, f"{path} entries malformed"
        return have == want, f"{path}: {[(Path(p).name, s[:12]) for p, s in have]}"
    return Check(f"spec {path} lists the supplied files with the checker's sha256", fn)


def C_geometry(shape, length, height=None, density=None):
    def fn(imp, base):
        spec = imp["spec"] or {}
        found, geo = getpath(spec, "self_shielding.geometry")
        if not found or not isinstance(geo, dict):
            return False, "self_shielding.geometry absent"
        ok = geo.get("shape") == shape and same(geo.get("length_cm"), length)
        ok = ok and (same(geo.get("height_cm"), height) if height is not None else geo.get("height_cm") is None)
        dens = geo.get("density_g_cm3")
        if dens is None:
            dens = getpath(spec, "material.density_g_cm3")[1]
        if density is not None:
            ok = ok and dens is not None and same(dens, density)
        return ok, f"geometry {geo!r}, effective density {dens!r}"
    return Check(f"spec self_shielding.geometry is {shape}, length_cm {length}"
                 + (f", height_cm {height}" if height is not None else "") + f", density {density}", fn)


def C_resonance_files(files):
    return C_files("self_shielding.geometry.resonance_files", files)


def C_damage(table_path, table_sha, energies):
    want = {k.upper(): v for k, v in energies.items()}

    def fn(imp, base):
        found, dmg = getpath(imp["spec"] or {}, "damage")
        if not found or not isinstance(dmg, dict):
            return False, "damage absent"
        table = dmg.get("table") or {}
        ok = table.get("sha256") == table_sha and os.path.realpath(str(table.get("path", ""))) == os.path.realpath(table_path)
        got = {str(k).upper(): v for k, v in (dmg.get("displacement_energy_eV") or {}).items()}
        ok = ok and set(got) == set(want) and all(same(got[k], want[k]) for k in want)
        return ok, f"damage.table sha {str(table.get('sha256'))[:12]}, displacement_energy_eV {got!r}"
    return Check(f"spec damage.table is the supplied table (checker's sha256), displacement_energy_eV == {energies!r}", fn)


def C_override(nuclide, kind, mt, value):
    want = nuclide_key(nuclide)

    def fn(imp, base):
        found, ov = getpath(imp["spec"] or {}, "overrides")
        if not found or not isinstance(ov, list):
            return False, "overrides absent"
        hit = [o for o in ov if nuclide_key(o.get("nuclide", "")) == want and o.get("kind") == kind
               and o.get("mt") == mt and same(o.get("value"), value)]
        return len(hit) == 1, f"overrides {ov!r}"
    return Check(f"spec overrides has {{{nuclide}, {kind}, mt {mt}, value {value}}}", fn)


def C_no_override(kind):
    def fn(imp, base):
        found, ov = getpath(imp["spec"] or {}, "overrides")
        bad = [o for o in (ov if found and isinstance(ov, list) else []) if o.get("kind") == kind]
        return not bad, f"overrides of kind {kind}: {bad!r}"
    return Check(f"spec has no {kind} override", fn)


def C_no_unsupported(allowed=()):
    allowed = set(allowed)

    def fn(imp, base):
        bad = [k for k in unsupported_keywords(imp["report"]) if k not in allowed]
        return not bad, f"unsupported: {unsupported_keywords(imp['report'])}"
    return Check("no unsupported entry" + (f" except {sorted(allowed)}" if allowed else ""), fn)


def C_only_unsupported(keyword):
    return Check(f"{keyword} is the only unsupported keyword",
                 lambda imp, base: (unsupported_keywords(imp["report"]) == [keyword],
                                    f"unsupported: {unsupported_keywords(imp['report'])}"))


def C_vs_base(allowed_prefixes, expect_difference=False):
    """The spec differs from the base import (the same deck without the keyword) only under the prefixes given."""
    prefixes = tuple(allowed_prefixes)

    def fn(imp, base):
        if base is None or base["spec"] is None or imp["spec"] is None:
            return False, "no base import"
        diffs = json_diffs(base["spec"], imp["spec"])
        diffs = [d for d in diffs if d["path"] != "title"]      # the decks differ in their title line
        outside = [d["path"] for d in diffs if not any(path_under(d["path"], p) for p in prefixes)]
        ok = not outside and (bool(diffs) or not expect_difference)
        return ok, f"{len(diffs)} differences, outside {list(prefixes)}: {outside[:6]}"
    return Check("spec equals the base import (deck without the keyword)" if not prefixes else
                 f"spec equals the base import except under {list(prefixes)}", fn)


def C_outputs_photons(keywords):
    """Assumption A4: with an options.outputs list lacking `photons` in the base, the candidate's list gains it and the
    keyword's detail reports it; with no list, outputs stay absent (or gain photons)."""
    def fn(imp, base):
        base_out = getpath((base or {}).get("spec") or {}, "options.outputs")[1]
        out = getpath(imp["spec"] or {}, "options.outputs")[1]
        if isinstance(base_out, list):
            if "photons" in base_out:
                return out == base_out, f"outputs {out!r}"
            ok = isinstance(out, list) and set(out) == set(base_out) | {"photons"}
            said = any("photons" in detail_text(imp["report"], k) for k in keywords)
            return ok and said, f"outputs {out!r}, reported {said}"
        return out is None or (isinstance(out, list) and "photons" in out), f"outputs {out!r} (base had none)"
    return Check("options.outputs gains photons when a list lacks it, and the report says so", fn)


class Case:
    def __init__(self, cid, group, deck, args, checks, base_deck=None, gating=True, note=""):
        self.id, self.group, self.deck, self.args, self.checks = cid, group, deck, list(args), list(checks)
        self.base_deck, self.gating, self.note = base_deck, gating, note


# ------------------------------------------------------------------------------ G3 / G6 case catalogue


def yield_set(*names):
    return {yield_file_parent(n) for n in names}


def build_g3_cases(fx):
    """Every synthetic deck of G3 (and its checks). `fx` is a Fixtures."""
    cases = []
    U235, U238, PU239, TH232 = ("nfy-092_U_235.endf", "nfy-092_U_238.endf", "nfy-094_Pu_239.endf",
                                "nfy-090_Th_232.endf")
    four = (U235, U238, PU239, TH232)

    def yfiles(*names):
        return [(fx.yields[n]["path"], fx.yields[n]["sha256"]) for n in names]

    def add(cid, group, deck, args, checks, **kw):
        cases.append(Case(cid, group, deck, ["--fluxes", str(fx.flux709)] + list(args), [C_exit0()] + checks, **kw))

    # ---- item 2: fission
    add("fission_default_parents_all_files", "fission",
        make_deck("USEFISSION, four yield files", initial=["USEFISSION"], material=FUEL_MATERIAL), fx.yield_args(*four),
        [C_status("USEFISSION", ["mapped"]), C_files("fission_yields.files", yfiles(*four)),
         C_parents("include", set(DEFAULT_FISSION_PARENTS)), C_no_unsupported()])
    add("fission_default_parents_intersected", "fission",
        make_deck("USEFISSION, U-235 and Th-232 files", initial=["USEFISSION"], material=FUEL_MATERIAL),
        fx.yield_args(U235, TH232),
        [C_status("USEFISSION", ["mapped"]), C_files("fission_yields.files", yfiles(U235, TH232)),
         C_parents("include", {("U", 235, 0)}),
         Check("the report names the dropped default parents U238 and Pu239",
               lambda imp, base: (all(any(w in re.sub(r"[-\s]", "", str(e.get("detail", ""))).lower()
                                          for e in entries_of(imp["report"]))
                                      for w in ("u238", "pu239")), "searched every entry detail")),
         C_no_unsupported()])
    add("fission_nofiss", "fission",
        make_deck("USEFISSION with NOFISS", library=["NOFISS"], initial=["USEFISSION"], material=FUEL_MATERIAL),
        fx.yield_args(U235, U238),
        [C_status("NOFISS", ["mapped"]), C_absent("fission_yields.files")])
    add("fission_cumfyld", "fission",
        make_deck("CUMFYLD", library=["CUMFYLD"], initial=["USEFISSION"], material=FUEL_MATERIAL),
        fx.yield_args(U235, U238),
        [C_status("CUMFYLD", ["mapped"]), C_eq("fission_yields.yields", "cumulative"),
         C_files("fission_yields.files", yfiles(U235, U238)), C_no_unsupported()])
    add("fission_fisyield_positive", "fission",
        make_deck("FISYIELD 2 U235 PU239", initial=["USEFISSION", "FISYIELD 2 U235 PU239"], material=FUEL_MATERIAL),
        fx.yield_args(*four),
        [C_status("FISYIELD", ["mapped"]), C_parents("include", {("U", 235, 0), ("PU", 239, 0)}),
         C_no_unsupported()])
    add("fission_fisyield_negative", "fission",
        make_deck("FISYIELD -1 U238", initial=["USEFISSION", "FISYIELD -1 U238"], material=FUEL_MATERIAL),
        fx.yield_args(*four),
        [C_status("FISYIELD", ["mapped"]), C_parents("exclude", {("U", 238, 0)}), C_no_unsupported()])
    add("fission_fisyield_zero", "fission",
        make_deck("FISYIELD 0", initial=["USEFISSION", "FISYIELD 0"], material=FUEL_MATERIAL),
        fx.yield_args(U235, U238, PU239),
        [C_status("FISYIELD", ["mapped"]), C_parents("exclude", yield_set(U235, U238, PU239)), C_no_unsupported()])
    add("fission_fischoose", "fission",
        make_deck("FISCHOOSE", initial=["PATH 1 CS137", "FISCHOOSE 2 U235 PU239"], material=FUEL_MATERIAL), [],
        [C_status("FISCHOOSE", ["mapped"]), C_names("pathways.fission_parents", {("U", 235, 0), ("PU", 239, 0)}),
         C_no_unsupported()])
    add("info_fischoose_without_path", "fission",
        make_deck("FISCHOOSE without PATH", initial=["FISCHOOSE 1 U235"], material=FUEL_MATERIAL), [],
        [], gating=False, note="information: status and whether a pathways section is created")

    # ---- item 3: photon
    photon_base = make_deck("photon base")
    add("photon_noxray", "photon", make_deck("NOXRAY", library=["NOXRAY"]), [],
        [C_status("NOXRAY", ["mapped"]), C_eq("photon.exclude_xrays", True), C_outputs_photons(["NOXRAY"]),
         C_no_unsupported()], base_deck=photon_base)
    add("photon_spek", "photon", make_deck("SPEK", library=["SPEK"]), [],
        [C_status("SPEK", ["approximated"]), C_eq("photon.missing_spectra", "estimate"),
         C_detail("SPEK", ["bound", "mean energy", "decay mode"], mode="any"), C_outputs_photons(["SPEK"]),
         C_no_unsupported()], base_deck=photon_base)
    add("photon_spek_noxray", "photon", make_deck("SPEK and NOXRAY", library=["NOXRAY", "SPEK"]), [],
        [C_status("SPEK", ["approximated"]), C_status("NOXRAY", ["mapped"]),
         C_eq("photon.missing_spectra", "estimate"), C_eq("photon.exclude_xrays", True),
         C_outputs_photons(["SPEK", "NOXRAY"]), C_no_unsupported()], base_deck=photon_base)
    add("photon_readgg", "photon", make_deck("READGG", library=["READGG"]), ["--files", str(fx.files_ggbins)],
        [C_status("READGG", ["mapped"]), C_eq("photon.group_boundaries_eV", GGBINS_GOOD),
         Check("photon.group_structure is absent or 'custom' (Amendment A3)",
               lambda imp, base: (getpath(imp["spec"] or {}, "photon.group_structure")[1] in (None, "custom"),
                                  str(getpath(imp["spec"] or {}, "photon.group_structure")[1]))),
         C_no_unsupported()])

    # ---- item 4: damage
    rec = fx.damage["recorded"]
    el, ed = fx.damage["first"], fx.damage["recorded"][fx.damage["first"]]
    fe_mat = lambda sym: ["DENSITY 7.874", "MASS 1.0E-3 1", f" {sym.upper()} 100.0"]
    dmg = lambda energies: C_damage(fx.damage["path"], fx.damage["sha256"], energies)
    add("damage_alldispen", "damage", make_deck("ALLDISPEN", library_late=[f"ALLDISPEN {ed!r}"], material=fe_mat(el)),
        fx.damage_args(), [C_status("ALLDISPEN", ["mapped"]), dmg({el: ed}), C_no_unsupported()])
    add("damage_atdispen", "damage",
        make_deck("ATDISPEN", library_late=[f"ATDISPEN 1 {el.upper()} {ed!r}"], material=fe_mat(el)),
        fx.damage_args(), [C_status("ATDISPEN", ["mapped"]), dmg({el: ed}), C_no_unsupported()])
    other = ed + 10.0
    add("damage_all_then_at", "damage",
        make_deck("ALLDISPEN then ATDISPEN", library_late=[f"ALLDISPEN {other!r}", f"ATDISPEN 1 {el.upper()} {ed!r}"],
                  material=fe_mat(el)),
        fx.damage_args(), [C_status("ALLDISPEN", ["mapped"]), C_status("ATDISPEN", ["mapped"]), dmg({el: ed}),
                           C_no_unsupported()])
    add("damage_at_then_all", "damage",
        make_deck("ATDISPEN then ALLDISPEN", library_late=[f"ATDISPEN 1 {el.upper()} {ed!r}", f"ALLDISPEN {other!r}"],
                  material=fe_mat(el)),
        fx.damage_args(), [C_status("ALLDISPEN", ["mapped"]), C_status("ATDISPEN", ["mapped"]), dmg({el: ed}),
                           C_no_unsupported()])
    second = fx.damage.get("second")
    if second:
        e2 = rec[second]
        for order, lines in (("all_then_at", [f"ALLDISPEN {e2!r}", f"ATDISPEN 1 {el.upper()} {ed!r}"]),
                             ("at_then_all", [f"ATDISPEN 1 {el.upper()} {ed!r}", f"ALLDISPEN {e2!r}"])):
            add(f"damage_two_elements_{order}", "damage",
                make_deck("two elements " + order, library_late=lines,
                          material=["DENSITY 7.0", "MASS 1.0E-3 2", f" {el.upper()} 50.0", f" {second.upper()} 50.0"]),
                fx.damage_args(), [C_status("ALLDISPEN", ["mapped"]), C_status("ATDISPEN", ["mapped"]),
                                   dmg({el: ed, second: e2}), C_no_unsupported()])

    # Amendment A2: displacement energies the deck does not give
    def displacement_entry():
        return Check("one DISPLACEMENT entry, mapped or approximated",
                     lambda imp, base: (len(entries_of(imp["report"], "DISPLACEMENT")) == 1 and all(
                         e["status"] in ("mapped", "approximated") for e in entries_of(imp["report"], "DISPLACEMENT")),
                         str([(e["status"], e.get("detail", "")[:80]) for e in entries_of(imp["report"], "DISPLACEMENT")])))
    add("damage_no_keyword", "damage", make_deck("--damage without ALLDISPEN or ATDISPEN", material=fe_mat(el)),
        fx.damage_args(), [displacement_entry(), dmg({el: ed}), C_no_unsupported()])
    if second:
        e2 = rec[second]
        two = ["DENSITY 7.0", "MASS 1.0E-3 2", f" {el.upper()} 50.0", f" {second.upper()} 50.0"]
        add("damage_no_keyword_two_elements", "damage", make_deck("--damage, two elements, no keyword", material=two),
            fx.damage_args(), [displacement_entry(), dmg({el: ed, second: e2}), C_no_unsupported()])
        add("damage_atdispen_other_element_recorded", "damage",
            make_deck("ATDISPEN gives one element, the other takes the recorded value",
                      library_late=[f"ATDISPEN 1 {el.upper()} {ed!r}"], material=two),
            fx.damage_args(), [C_status("ATDISPEN", ["mapped"]), dmg({el: ed, second: e2}), C_no_unsupported()])
    absent_el = next((e for e in ("W", "Cu", "Ni", "Ti", "Al") if e.upper() != el.upper()), "W")
    add("damage_atdispen_element_not_in_material", "damage",
        make_deck("ATDISPEN names an element the material lacks",
                  library_late=[f"ATDISPEN 2 {el.upper()} {ed!r} {absent_el.upper()} 30.0"], material=fe_mat(el)),
        fx.damage_args(),
        [dmg({el: ed}), C_no_unsupported(),
         Check(f"the report says {absent_el} is not in the material",
               lambda imp, base: (any(absent_el.lower() in str(e.get("detail", "")).lower()
                                      for e in entries_of(imp["report"]) if e["keyword"] in ("ATDISPEN", "DISPLACEMENT")),
                                  "searched ATDISPEN and DISPLACEMENT details"))])

    # ---- item 5: uncertainty
    unc = ["UNCERTAINTY 2"]
    for n, channels in ((1, {"cross_section_mf33"}), (2, {"decay_constants"}),
                        (3, {"cross_section_mf33", "decay_constants"})):
        # Amendment A1: UNCTYPE 2 is approximated (the MF=33 covariance is still propagated), 1 and 3 are mapped
        extra = [C_detail("UNCTYPE", ["mf=33", "mf33", "covariance"], mode="any")] if n == 2 else []
        add(f"uncertainty_unctype_{n}", "uncertainty", make_deck(f"UNCTYPE {n}", initial=unc + [f"UNCTYPE {n}"]), [],
            [C_status("UNCTYPE", ["approximated" if n == 2 else "mapped"]),
             C_channels(channels), C_no_unsupported()] + extra)
    add("uncertainty_covariance", "uncertainty", make_deck("COVARIANCE", library=["COVARIANCE"], initial=unc), [],
        [C_status("COVARIANCE", ["noted"]), C_vs_base([]), C_no_unsupported()],
        base_deck=make_deck("COVARIANCE base", initial=unc))
    sens = ["SENSITIVITY SIGMA 0.8 2 1", " FE56 MN56", " FE54 MN54", " MN56"]
    add("uncertainty_sensitivity_sigma", "uncertainty", make_deck("SENSITIVITY SIGMA", initial=unc + sens), [],
        [C_status("SENSITIVITY", ["approximated"]),
         Check("spec uncertainty.voi.top is a positive integer",
               lambda imp, base: ((lambda f, v: f and isinstance(v, dict) and isinstance(v.get("top"), int)
                                   and v["top"] >= 1)(*getpath(imp["spec"] or {}, "uncertainty.voi")),
                                  str(getpath(imp["spec"] or {}, "uncertainty.voi")[1]))),
         C_no_unsupported()])
    add("uncertainty_sensitivity_lambda", "uncertainty",
        make_deck("SENSITIVITY LAMBDA", initial=unc + ["SENSITIVITY LAMBDA 0.8 1 1", " MN56", " MN56"]), [],
        [C_status("SENSITIVITY", ["unsupported"]), C_detail("SENSITIVITY", ["lambda"]), C_absent("uncertainty.voi")])
    add("uncertainty_mcsample_with_sensitivity", "uncertainty",
        make_deck("MCSAMPLE with SENSITIVITY SIGMA", initial=unc + sens + ["MCSAMPLE 1 100 -3.0 3.0", "MCSEED 12345"]),
        [], [C_status("SENSITIVITY", ["approximated"]), C_status("MCSAMPLE", ["noted"]), C_status("MCSEED", ["noted"]),
             C_no_unsupported()])
    add("uncertainty_mcsample_without_sensitivity", "uncertainty",
        make_deck("MCSAMPLE alone", initial=unc + ["MCSAMPLE 1 100 -3.0 3.0"]), [],
        [C_status("MCSAMPLE", ["unsupported"]), C_detail("MCSAMPLE", ["sensitivity"])])
    add("uncertainty_mcsample_with_lambda", "uncertainty",
        make_deck("MCSAMPLE with SENSITIVITY LAMBDA",
                  initial=unc + ["SENSITIVITY LAMBDA 0.8 1 1", " MN56", " MN56", "MCSAMPLE 1 100 -3.0 3.0"]), [],
        [C_status("SENSITIVITY", ["unsupported"]), C_status("MCSAMPLE", ["unsupported"])])
    add("uncertainty_error_value", "uncertainty",
        make_deck("ERROR 0.15", initial=unc + sens + ["ERROR 1", " FE56 MN56 0.15"]), [],
        [C_status("ERROR", ["approximated"]), C_override("Fe56", "cross_section_uncertainty", 103, 0.15),
         C_detail("ERROR", ["relative"]), C_no_unsupported()])
    add("uncertainty_error_library_value", "uncertainty",
        make_deck("ERROR -1", initial=unc + sens + ["ERROR 1", " FE56 MN56 -1.0"]), [],
        [C_status("ERROR", ["noted"]), C_no_override("cross_section_uncertainty"), C_no_unsupported()])
    add("info_sensitivity_without_uncertainty", "uncertainty",
        make_deck("SENSITIVITY SIGMA without UNCERTAINTY", initial=sens), [], [], gating=False,
        note="information: status of SENSITIVITY SIGMA with no UNCERTAINTY keyword")

    # ---- item 6: self-shielding
    ssf_mass = ["SSFMASS 0.000193 1", " W 100.0"]
    for cid, line, shape, length, height in (("foil", "SSFGEOMETRY 1 0.01", "foil", 0.01, None),
                                             ("wire", "SSFGEOMETRY 2 0.05", "wire", 0.05, None),
                                             ("sphere", "SSFGEOMETRY 3 0.5", "sphere", 0.5, None),
                                             ("cylinder", "SSFGEOMETRY 4 0.5 2.0", "cylinder", 0.5, 2.0)):
        add(f"selfshield_geometry_{cid}", "self_shielding",
            make_deck(f"SSFGEOMETRY {cid}", material=TUNGSTEN, initial=ssf_mass + [line]), fx.resonance_args(),
            [C_status("SSFGEOMETRY", ["mapped"]), C_status("SSFMASS", ["mapped", "approximated", "noted"]),
             C_geometry(shape, length, height, 19.3), C_resonance_files([(str(p), sha256_file(p)) for p in fx.resonance]),
             C_no_unsupported()])
    add("selfshield_geometry_choose_fuel", "self_shielding",
        make_deck("SSFGEOMETRY with SSFCHOOSE and SSFFUEL", material=TUNGSTEN,
                  initial=["SSFCHOOSE 2 0", " W182 W184", "SSFFUEL 2", " W182 1.0E+22", " W184 1.0E+22",
                           "SSFGEOMETRY 1 0.01"]),
        fx.resonance_args(),
        [C_status("SSFGEOMETRY", ["mapped"]), C_status("SSFCHOOSE", ["mapped", "approximated", "noted"]),
         C_status("SSFFUEL", ["mapped", "approximated", "noted"]), C_geometry("foil", 0.01, None, 19.3),
         C_no_unsupported()])
    add("selfshield_geometry_type5", "self_shielding",
        make_deck("SSFGEOMETRY 5", material=TUNGSTEN, initial=ssf_mass + ["SSFGEOMETRY 5 0.01"]), fx.resonance_args(),
        [C_status("SSFGEOMETRY", ["unsupported"]), C_detail("SSFGEOMETRY", ["shape", "type"], mode="any"),
         C_absent("self_shielding.geometry")])
    table_checks = [C_eq("self_shielding.table.sha256", sha256_file(fx.table))]
    add("selfshield_probtable", "self_shielding",
        make_deck("PROBTABLE", material=TUNGSTEN, initial=["SSFCHOOSE 2 0", " W182 W184", "PROBTABLE 1 0"]),
        fx.table_args(),
        [C_status("PROBTABLE", ["approximated"]), C_detail("PROBTABLE", ["bondarenko"])] + table_checks
        + [C_no_unsupported(["SSFCHOOSE"])])
    dil = ["SSFDILUTION 2", " W182 2", " 300 100.0 301 80.0", " W184 2", " 194 2.5", " 200 10.0"]
    expected_dil = {"W182": {str(dilution_index(300)): 100.0, str(dilution_index(301)): 80.0},
                    "W184": {str(dilution_index(194)): 2.5, str(dilution_index(200)): 10.0}}
    add("selfshield_ssfdilution", "self_shielding",
        make_deck("SSFDILUTION", material=TUNGSTEN, initial=["SSFCHOOSE 2 0", " W182 W184", "PROBTABLE 1 0"] + dil),
        fx.table_args(),
        [C_status("SSFDILUTION", ["mapped"]), C_status("PROBTABLE", ["approximated"]),
         C_eq("self_shielding.dilution_overrides", expected_dil), C_no_unsupported(["SSFCHOOSE"])])
    add("info_ssfdilution_without_probtable", "self_shielding",
        make_deck("SSFDILUTION alone", material=TUNGSTEN, initial=dil), fx.table_args(), [], gating=False,
        note="information: SSFDILUTION with the table option and no PROBTABLE")

    # ---- item 7: corrections and what stays unsupported
    add("correction_xsthreshold", "corrections", make_deck("XSTHRESHOLD", library=["XSTHRESHOLD 1E-12"]), [],
        [C_status("XSTHRESHOLD", ["noted"]), C_no_unsupported()])
    add("correction_depletion", "corrections", make_deck("DEPLETION", initial=["DEPLETION 2 FE56 FE54"]), [],
        [C_status("DEPLETION", ["noted"]), C_no_unsupported()])
    add("correction_result_with_inventory", "corrections",
        make_deck("RESULT with an inventory", initial=["PATH 1 MN54"],
                  schedule=["FLUX 1.0E+12", "RESULT 1 FE56 1.0E+19", "TIME 1 HOURS ATOMS", "FLUX 0.", "ZERO",
                            "TIME 1 DAYS ATOMS"]), [],
        [C_status("RESULT", ["noted"]), C_no_unsupported()])
    add("correction_iron_reason", "corrections", make_deck("IRON", initial=["IRON"]), [],
        [C_status("IRON", ["unsupported", "noted"]),
         Check("IRON's detail no longer describes iron background removal",
               lambda imp, base: (bool(detail_text(imp["report"], "IRON"))
                                  and not any(w in detail_text(imp["report"], "IRON") for w in OLD_IRON_WORDS),
                                  detail_text(imp["report"], "IRON")[:200]))])
    add("kept_readsf", "kept_unsupported", make_deck("READSF", library=["READSF"]), [],
        [C_status("READSF", ["unsupported"]), C_only_unsupported("READSF")])
    add("kept_usespallation", "kept_unsupported",
        make_deck("USESPALLATION", library=["USESPALLATION 100.0 1", " PB208"]), [],
        [C_status("USESPALLATION", ["unsupported"]), C_only_unsupported("USESPALLATION")])
    add("kept_usexsextra", "kept_unsupported", make_deck("USEXSEXTRA", library=["USEXSEXTRA"]), [],
        [C_status("USEXSEXTRA", ["unsupported"]), C_only_unsupported("USEXSEXTRA")])
    add("kept_mid_schedule_getxs", "kept_unsupported",
        make_deck("mid-schedule GETXS",
                  schedule=["FLUX 1.0E+12", "TIME 1 HOURS ATOMS", "GETXS 1 709", "TIME 1 HOURS ATOMS"]), [],
        [C_status("GETXS", ["unsupported"], phase="inventory"), C_only_unsupported("GETXS")])
    add("kept_mid_schedule_getdecay", "kept_unsupported",
        make_deck("mid-schedule GETDECAY",
                  schedule=["FLUX 1.0E+12", "TIME 1 HOURS ATOMS", "GETDECAY 1", "TIME 1 HOURS ATOMS"]), [],
        [C_status("GETDECAY", ["unsupported"], phase="inventory"), C_only_unsupported("GETDECAY")])
    add("kept_over_adcross", "kept_unsupported", make_deck("OVER ADCROSS", initial=["OVER FE56", " ADCROSS MN56 0.1"]), [],
        [C_status("ADCROSS", ["unsupported"]), C_no_unsupported(["ADCROSS", "OVER"])])
    add("kept_over_adlam", "kept_unsupported",
        make_deck("OVER ADLAM", initial=["OVER MN56", " ALAM 100.0 MINS", " ADLAM 0.1"]), [],
        [C_status("ADLAM", ["unsupported"]), C_no_unsupported(["ADLAM", "OVER"])])
    add("info_result_without_inventory", "corrections",
        "FISPACT\n* RESULT without an inventory\nPATH 1 CO60\nRESULT 1 FE56 1.0E+19\nEND\n* end\n", [], [],
        gating=False, note="information: RESULT in a deck that defines no inventory (protocol: unsupported)")
    return cases


def build_g6_cases(fx):
    """Refusals: (case id, deck, args, keyword, terms that must all be in the detail, spec paths that must be empty)."""
    el, ed = fx.damage["first"], fx.damage["recorded"][fx.damage["first"]]
    bad_ed = ed + 7.5
    mat = ["DENSITY 7.874", "MASS 1.0E-3 1", f" {el.upper()} 100.0"]
    ssf_mass = ["SSFMASS 0.000193 1", " W 100.0"]
    dil = ["SSFDILUTION 1", " W182 1", " 300 100.0"]
    unc = ["UNCERTAINTY 2"]
    cases = [
        ("missing_option_usefission", make_deck("USEFISSION without --fission-yields", initial=["USEFISSION"],
                                                material=FUEL_MATERIAL), [], "USEFISSION", ["--fission-yields"],
         ["fission_yields.files"]),
        ("missing_option_alldispen", make_deck("ALLDISPEN without --damage", library_late=[f"ALLDISPEN {ed!r}"],
                                               material=mat), [], "ALLDISPEN", ["--damage"], ["damage"]),
        ("missing_option_atdispen", make_deck("ATDISPEN without --damage",
                                              library_late=[f"ATDISPEN 1 {el.upper()} {ed!r}"], material=mat), [],
         "ATDISPEN", ["--damage"], ["damage"]),
        ("missing_option_probtable", make_deck("PROBTABLE without --self-shielding-table", material=TUNGSTEN,
                                               initial=["SSFCHOOSE 2 0", " W182 W184", "PROBTABLE 1 0"]), [],
         "PROBTABLE", ["--self-shielding-table"], ["self_shielding"]),
        ("missing_option_ssfdilution", make_deck("SSFDILUTION without --self-shielding-table", material=TUNGSTEN,
                                                 initial=dil), [], "SSFDILUTION", ["--self-shielding-table"],
         ["self_shielding"]),
        ("missing_option_ssfgeometry", make_deck("SSFGEOMETRY without --resonance", material=TUNGSTEN,
                                                 initial=ssf_mass + ["SSFGEOMETRY 1 0.01"]), [], "SSFGEOMETRY",
         ["--resonance"], ["self_shielding"]),
        ("damage_mismatch_alldispen", make_deck("ALLDISPEN differs from the table",
                                                library_late=[f"ALLDISPEN {bad_ed!r}"], material=mat),
         fx.damage_args(), "ALLDISPEN", ["build-damage", "kinematic", "displacement-energy"], ["damage"]),
        ("damage_mismatch_atdispen", make_deck("ATDISPEN differs from the table",
                                               library_late=[f"ATDISPEN 1 {el.upper()} {bad_ed!r}"], material=mat),
         fx.damage_args(), "ATDISPEN", ["build-damage", "kinematic", "displacement-energy"], ["damage"]),
        ("damage_mismatch_override_order", make_deck("ATDISPEN differs, ALLDISPEN matches: the per-element value wins",
                                                     library_late=[f"ATDISPEN 1 {el.upper()} {bad_ed!r}",
                                                                   f"ALLDISPEN {ed!r}"], material=mat),
         fx.damage_args(), ("ATDISPEN", "ALLDISPEN"), ["build-damage", "kinematic", "displacement-energy"],
         ["damage"]),
        ("sensitivity_lambda", make_deck("SENSITIVITY LAMBDA", initial=unc + ["SENSITIVITY LAMBDA 0.8 1 1", " MN56",
                                                                              " MN56"]), [], "SENSITIVITY",
         ["lambda"], ["uncertainty.voi"]),
        ("mcsample_without_sensitivity", make_deck("MCSAMPLE alone", initial=unc + ["MCSAMPLE 1 100 -3.0 3.0"]), [],
         "MCSAMPLE", ["sensitivity"], []),
    ]
    for name in list(GGBINS_BAD) + ["missing_entry", "nonexistent_file"]:
        cases.append((f"ggbins_{name}", make_deck(f"READGG, ggbins: {name}", library=["READGG"]),
                      ["--files", str(fx.bad[name])], "READGG", ["ggbins"], ["photon.group_boundaries_eV"]))
    cases.append(("ggbins_no_files_file", make_deck("READGG without a files file", library=["READGG"]), [], "READGG",
                  ["ggbins"], ["photon.group_boundaries_eV"]))
    out = []
    for cid, deck, args, keyword, terms, forbid in cases:
        out.append({"id": cid, "deck": deck, "args": ["--fluxes", str(fx.flux709)] + list(args),
                    "keyword": keyword, "terms": terms, "forbid": forbid})
    return out


# ------------------------------------------------------------------------------ gate G2


def diff_reports(ref, cand):
    """Differences between two import reports: entry status/detail/args changes, entries on one side only, and other
    report fields. `counts` is checked for consistency with the candidate's own entries instead of equality."""
    def keyed(report):
        seen, out = {}, {}
        for e in entries_of(report):
            k = (e["keyword"], e["line"])
            out[(k, seen.get(k, 0))] = e
            seen[k] = seen.get(k, 0) + 1
        return out
    a, b = keyed(ref), keyed(cand)
    diffs = []
    for key in sorted(set(a) | set(b), key=lambda k: (k[0][1], k[0][0], k[1])):
        keyword, line = key[0]
        ea, eb = a.get(key), b.get(key)
        row = {"keyword": keyword, "line": line}
        if ea is None or eb is None:
            diffs.append({**row, "what": "entry only in the " + ("candidate" if ea is None else "reference"),
                          "ref": None if ea is None else ea["status"], "cand": None if eb is None else eb["status"]})
            continue
        if ea["status"] != eb["status"]:
            diffs.append({**row, "what": "status", "ref": ea["status"], "cand": eb["status"],
                          "cand_detail": eb.get("detail")})
        elif ea.get("detail") != eb.get("detail"):
            diffs.append({**row, "what": "detail", "ref": ea.get("detail"), "cand": eb.get("detail")})
        if ea.get("phase") != eb.get("phase") or ea.get("args") != eb.get("args"):
            diffs.append({**row, "what": "phase or args", "ref": [ea.get("phase"), ea.get("args")],
                          "cand": [eb.get("phase"), eb.get("args")]})
    for field in sorted((set(ref) | set(cand)) - {"entries", "counts", "deck", "files_file"}):
        if ref.get(field) != cand.get(field):
            diffs.append({"keyword": None, "line": None, "what": f"report field {field}",
                          "ref": ref.get(field), "cand": cand.get(field)})
    if cand.get("counts") != tally(cand) and not all(cand.get("counts", {}).get(s) == tally(cand)[s]
                                                       for s in STATUSES):
        diffs.append({"keyword": None, "line": None, "what": "counts disagree with the entries",
                      "ref": None, "cand": cand.get("counts")})
    return diffs


def attribute(report_diffs, spec_diffs, keywords_in_deck):
    """Attach to every difference the keyword it is attributed to; `attributed` False when none applies."""
    out = []
    for d in report_diffs:
        kw = d.get("keyword")
        ok = kw in ITEM_KEYWORDS
        out.append({**d, "where": "report", "attributed_to": [kw] if ok else [], "attributed": ok})
    for d in spec_diffs:
        owners = spec_owners(d["path"])
        present = sorted((owners or frozenset()) & set(keywords_in_deck))
        out.append({**d, "where": "spec", "attributed_to": present, "attributed": bool(present)})
    return out


def gate_g2(run, ref, cand, inputs):
    out = {}
    # ---- FNS corpus
    decks, _ = fns_files(inputs["fns_root"])
    rows, bad = [], []
    for deck in decks:
        tag = f"{deck.parent.name}__{deck.stem}"
        a = run.import_deck(ref, deck, run.work / "g2" / "fns" / "ref" / f"{tag}.json")
        b = run.import_deck(cand, deck, run.work / "g2" / "fns" / "cand" / f"{tag}.json")
        row = {"deck": f"{deck.parent.name}/{deck.name}", "exit_ref": a["exit"], "exit_cand": b["exit"],
               "spec_identical": a["spec_bytes"] == b["spec_bytes"] and a["spec_bytes"] is not None,
               "statuses_identical": status_rows(a["report"]) == status_rows(b["report"]) and a["report"] is not None,
               "detail_changes": sum(1 for x, y in zip(entries_of(a["report"]), entries_of(b["report"]))
                                     if x.get("detail") != y.get("detail"))}
        row["pass"] = a["exit"] == 0 and b["exit"] == 0 and row["spec_identical"] and row["statuses_identical"]
        rows.append(row)
        if not row["pass"]:
            bad.append(row)
    out["fns"] = {"decks": len(rows), "identical": sum(1 for r in rows if r["pass"]),
                  "decks_with_report_detail_changes": sum(1 for r in rows if r["detail_changes"]),
                  "failures": bad[:MAX_LISTED], "pass": len(rows) == EXPECTED_FNS_DECKS and not bad}
    # ---- pypact and CONDERC decks without new options
    corpus = []
    for rel in sorted(PYPACT_PINS):
        corpus.append(("pypact", rel.replace("/", "__"), inputs["pypact_root"] / rel, None))
    for name in ("U2352E4s.i", "U235pulse.i"):
        corpus.append(("conderc", name, inputs["conderc_root"] / name, inputs["conderc_root"] / "fluxes_therm"))
    cases = {}
    for group, tag, deck, fluxes in corpus:
        if fluxes is None:
            proton = re.search(r"^\s*PROJECTILE\s+([2-7])\b", deck.read_text(errors="replace"), re.M | re.I)
            fluxes = inputs["flux162"] if proton else inputs["flux709"]
        extra = ["--fluxes", str(fluxes)]
        a = run.import_deck(ref, deck, run.work / "g2" / group / "ref" / f"{tag}.json", extra)
        b = run.import_deck(cand, deck, run.work / "g2" / group / "cand" / f"{tag}.json", extra)
        row = {"group": group, "exit_ref": a["exit"], "exit_cand": b["exit"],
               "fluxes": str(fluxes)}
        if a["exit"] != 0 or b["exit"] != 0 or a["spec"] is None or b["spec"] is None:
            row.update({"pass": a["exit"] == b["exit"] and a["exit"] != 0
                        and a["stderr"].strip().splitlines()[-1:] == b["stderr"].strip().splitlines()[-1:],
                        "note": "an import failed; equal failures pass only when both exits and messages agree",
                        "stderr_ref": a["stderr"][-200:], "stderr_cand": b["stderr"][-200:]})
            cases[tag] = row
            continue
        kws = {e["keyword"] for e in entries_of(b["report"])} | {e["keyword"] for e in entries_of(a["report"])}
        diffs = attribute(diff_reports(a["report"], b["report"]), json_diffs(a["spec"], b["spec"]), kws)
        row["differences"] = diffs[:200]
        row["n_differences"] = len(diffs)
        row["unattributed"] = [d for d in diffs if not d["attributed"]][:MAX_LISTED]
        row["keywords_with_differences"] = sorted({k for d in diffs for k in d["attributed_to"]})
        row["pass"] = not row["unattributed"]
        cases[tag] = row
    out["option_free"] = {"cases": cases, "pass": bool(cases) and all(c["pass"] for c in cases.values())}
    out["pass"] = out["fns"]["pass"] and out["option_free"]["pass"]
    return out


# ------------------------------------------------------------------------------ gates G3, G6


def run_case(run, binary, case, fx):
    deck = write_text(run.work / "decks" / f"{case.id}.i", case.deck)
    imp = run.import_deck(binary, deck, run.work / "g3" / case.id / "spec.json", case.args)
    base = None
    if case.base_deck is not None:
        base_deck = write_text(run.work / "decks" / f"{case.id}.base.i", case.base_deck)
        base = run.import_deck(binary, base_deck, run.work / "g3" / case.id / "base.json", case.args)
    results = []
    for chk in case.checks:
        try:
            ok, observed = chk.fn(imp, base)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError) as error:
            ok, observed = False, f"check error {type(error).__name__}: {error}"
        results.append({"expect": chk.desc, "ok": bool(ok), "observed": observed})
    row = {"id": case.id, "group": case.group, "gating": case.gating, "exit": imp["exit"],
           "unsupported": unsupported_keywords(imp["report"]),
           "statuses": {k: [e["status"] for e in entries_of(imp["report"], k)]
                        for k in sorted({e["keyword"] for e in entries_of(imp["report"])} & ITEM_KEYWORDS)},
           "checks": results, "pass": all(r["ok"] for r in results), "deck": str(deck)}
    if case.note:
        row["note"] = case.note
    if not case.gating:
        row["information"] = {"exit": imp["exit"], "stderr": imp["stderr"].strip()[-200:],
                              "details": {e["keyword"]: [e["status"], e.get("detail")]
                                          for e in entries_of(imp["report"]) if e["keyword"] in ITEM_KEYWORDS},
                              "spec_keys": sorted((imp["spec"] or {}).keys())}
    return row


def gate_g3(run, cand, fx):
    rows = [run_case(run, cand, c, fx) for c in build_g3_cases(fx)]
    gating = [r for r in rows if r["gating"]]
    return {"cases": rows, "n_gating": len(gating), "n_pass": sum(1 for r in gating if r["pass"]),
            "failures": [r["id"] for r in gating if not r["pass"]], "pass": bool(gating) and all(r["pass"] for r in gating)}


def gate_g6(run, ref, cand, fx):
    rows = []
    mismatch = fx.damage.get("mismatch")
    cases = build_g6_cases(fx)
    if mismatch:
        el, ed = fx.damage["first"], fx.damage["recorded"][fx.damage["first"]]
        cases.append({"id": "damage_second_table_mismatch",
                      "deck": make_deck("second damage table records another displacement energy",
                                        library_late=[f"ALLDISPEN {ed!r}"],
                                        material=["DENSITY 7.874", "MASS 1.0E-3 1", f" {el.upper()} 100.0"]),
                      "args": ["--fluxes", str(fx.flux709), "--damage", mismatch["path"]], "keyword": "ALLDISPEN",
                      "terms": ["build-damage", "kinematic", "displacement-energy"], "forbid": ["damage"]})
    for c in cases:
        deck = write_text(run.work / "decks" / f"g6_{c['id']}.i", c["deck"])
        out = run.work / "g6" / c["id"] / "spec.json"
        imp = run.import_deck(cand, deck, out, c["args"])
        kws = (c["keyword"],) if isinstance(c["keyword"], str) else tuple(c["keyword"])
        text = " | ".join(detail_text(imp["report"], k, "unsupported") for k in kws)
        row = {"id": c["id"], "keyword": list(kws), "exit": imp["exit"],
               "unsupported_keywords": unsupported_keywords(imp["report"]), "detail": text[:300]}
        row["entry_unsupported"] = any(entries_of(imp["report"], k, status="unsupported") for k in kws)
        row["reason_names_cause"] = bool(text.strip(" |")) and all(t.lower() in text for t in c["terms"])
        row["terms"] = c["terms"]
        spec = imp["spec"] or {}
        row["fragment_absent"] = all(empty(getpath(spec, p)[1]) for p in c["forbid"])
        # --strict: exit nonzero, no spec written (a stale spec from an earlier run is removed), --strict named
        strict_out = run.work / "g6" / c["id"] / "strict.json"
        stale = write_text(strict_out, "{\"stale\": true}\n")
        for p in (Path(report_path_of(strict_out)),):
            if p.exists():
                p.unlink()
        code, so, se = run_cmd([cand, "import-fispact", deck, strict_out] + c["args"] + ["--strict"],
                               run.import_timeout, run.cwd, run.env)
        err = se.decode(errors="replace")
        row["strict"] = {"exit": code, "spec_or_stale_file_left": stale.exists(), "names_strict": "--strict" in err,
                         "stderr": err.strip()[-200:]}
        row["strict_ok"] = bool(code not in (0, None) and not stale.exists() and "--strict" in err)
        row["pass"] = bool(imp["exit"] == 0 and row["entry_unsupported"] and row["reason_names_cause"]
                           and row["fragment_absent"] and row["strict_ok"])
        rows.append(row)
    return {"cases": rows, "failures": [r["id"] for r in rows if not r["pass"]],
            "pass": bool(rows) and all(r["pass"] for r in rows)}


# ------------------------------------------------------------------------------ gate G4


def hand_spec(imported, blocks, outputs="keep"):
    """The imported spec with the named top-level blocks replaced by the checker's own (None removes the block) and,
    optionally, options.outputs replaced."""
    spec = copy.deepcopy(imported)
    for key, value in blocks.items():
        if value is None:
            spec.pop(key, None)
        else:
            spec[key] = value
    if outputs != "keep":
        opts = spec.setdefault("options", {})
        if outputs is None:
            opts.pop("outputs", None)
        else:
            opts["outputs"] = outputs
    return spec


def ordered_like(imported_list, expected_names):
    """The imported list when it names the expected set (so ordering differences do not show up as run differences),
    else the expected names in the checker's order."""
    keys = {nuclide_key(x) for x in imported_list} if isinstance(imported_list, list) else set()
    return list(imported_list) if keys == {nuclide_key(x) for x in expected_names} else list(expected_names)


def has_fission_products(result):
    wanted = {"Xe135", "I131", "Cs137", "Mo99", "Ba140", "Zr95", "I135", "Sr90"}
    try:
        inventory = result["steps"][-1]["inventory"]
        return any(row["nuclide"] in wanted for row in inventory)
    except (KeyError, IndexError, TypeError):
        return False


def g4_cases(fx):
    """(id, deck, args, spec checks, base deck for outputs or None, builder of the hand blocks, vacuity test)."""
    U235, U238 = "nfy-092_U_235.endf", "nfy-092_U_238.endf"
    files2 = [(fx.yields[n]["path"], fx.yields[n]["sha256"]) for n in (U235, U238)]
    el, ed = fx.damage["first"], fx.damage["recorded"][fx.damage["first"]]
    cases = []

    def fission_blocks(extra, parents_kind, parents):
        def build(imported):
            found, imp_files = getpath(imported, "fission_yields.files")
            by_real = {os.path.realpath(e.get("path", "")): e.get("path") for e in (imp_files or [])
                       if isinstance(e, dict)}
            files = [{"path": by_real.get(os.path.realpath(p), p), "sha256": s} for p, s in files2]
            block = {"files": files, **extra}
            parents_imp = getpath(imported, f"fission_yields.parents.{parents_kind}")[1]
            block["parents"] = {parents_kind: ordered_like(parents_imp, parents)}
            return {"fission_yields": block}, "keep"
        return build

    cases.append({
        "id": "fission_cumfyld_fisyield", "kind": "fission",
        "deck": make_deck("P155 G4 fission, CUMFYLD, FISYIELD 1 U235", library=["CUMFYLD"],
                          initial=["USEFISSION", "FISYIELD 1 U235"], material=FUEL_MATERIAL,
                          schedule=["FLUX 1.0E+12", "TIME 1 HOURS ATOMS", "FLUX 0.", "ZERO", "TIME 1 DAYS ATOMS"]),
        "args": fx.yield_args(U235, U238),
        "checks": [C_exit0(), C_files("fission_yields.files", files2), C_eq("fission_yields.yields", "cumulative"),
                   C_parents("include", {("U", 235, 0)})],
        "base": None, "hand": fission_blocks({"yields": "cumulative"}, "include", ["U235"])})
    cases.append({
        "id": "fission_default_parents", "kind": "fission",
        "deck": make_deck("P155 G4 fission, default parent set", initial=["USEFISSION"], material=FUEL_MATERIAL),
        "args": fx.yield_args(U235, U238),
        "checks": [C_exit0(), C_files("fission_yields.files", files2), C_parents("include", {("U", 235, 0), ("U", 238, 0)})],
        "base": None, "hand": fission_blocks({}, "include", ["U235", "U238"])})
    cases.append({
        "id": "photon_spek_noxray", "kind": "photon",
        "deck": make_deck("P155 G4 photon, SPEK and NOXRAY", library=["NOXRAY", "SPEK"],
                          material=["DENSITY 7.8", "MASS 1.0E-3 3", " FE 70.0", " CR 20.0", " NI 10.0"]),
        "args": [], "checks": [C_exit0(), C_eq("photon.missing_spectra", "estimate"), C_eq("photon.exclude_xrays", True)],
        "base": make_deck("P155 G4 photon, SPEK and NOXRAY", material=["DENSITY 7.8", "MASS 1.0E-3 3", " FE 70.0",
                                                                         " CR 20.0", " NI 10.0"]),
        "hand": lambda imported: ({"photon": {**{k: v for k, v in (imported.get("photon") or {}).items()
                                                 if k not in ("missing_spectra", "exclude_xrays",
                                                              "group_boundaries_eV")},
                                              "missing_spectra": "estimate", "exclude_xrays": True}}, "keep")})
    cases.append({
        "id": "photon_readgg", "kind": "photon",
        "deck": make_deck("P155 G4 photon, READGG", library=["READGG"],
                          material=["DENSITY 7.8", "MASS 1.0E-3 3", " FE 70.0", " CR 20.0", " NI 10.0"]),
        "args": ["--files", str(fx.files_ggbins)],
        "checks": [C_exit0(), C_eq("photon.group_boundaries_eV", GGBINS_GOOD)], "base": None,
        "hand": lambda imported: ({"photon": {**{k: v for k, v in (imported.get("photon") or {}).items()
                                                 if k != "group_boundaries_eV"},
                                              "group_boundaries_eV": list(GGBINS_GOOD)}}, "keep")})

    def damage_blocks(imported):
        found, tbl = getpath(imported, "damage.table")
        path = tbl.get("path") if isinstance(tbl, dict) and tbl.get("path") else fx.damage["path"]
        return {"damage": {"table": {"path": path, "sha256": fx.damage["sha256"]},
                           "displacement_energy_eV": {el: ed}}}, "keep"
    cases.append({
        "id": "damage_alldispen", "kind": "damage",
        "deck": make_deck("P155 G4 damage, ALLDISPEN", library_late=[f"ALLDISPEN {ed!r}"],
                          material=["DENSITY 7.874", "MASS 1.0E-3 1", f" {el.upper()} 100.0"]),
        "args": fx.damage_args(),
        "checks": [C_exit0(), C_damage(fx.damage["path"], fx.damage["sha256"], {el: ed})], "base": None,
        "hand": damage_blocks})
    return cases


def gate_g4(run, cand, fx):
    rows = []
    for c in g4_cases(fx):
        row = {"id": c["id"], "kind": c["kind"]}
        try:
            deck = write_text(run.work / "decks" / f"g4_{c['id']}.i", c["deck"])
            imp = run.import_deck(cand, deck, run.work / "g4" / c["id"] / "spec.json",
                                  ["--fluxes", str(fx.flux709)] + list(c["args"]))
            base = None
            if c["base"] is not None:
                base_deck = write_text(run.work / "decks" / f"g4_{c['id']}.base.i", c["base"])
                base = run.import_deck(cand, base_deck, run.work / "g4" / c["id"] / "base.json",
                                       ["--fluxes", str(fx.flux709)] + list(c["args"]))
            checks = []
            for chk in c["checks"]:
                ok, observed = chk.fn(imp, base)
                checks.append({"expect": chk.desc, "ok": bool(ok), "observed": observed})
            if c["kind"] == "photon":
                ok, observed = C_outputs_photons(["SPEK", "NOXRAY", "READGG"]).fn(imp, base) \
                    if c["id"] == "photon_spek_noxray" else (True, "not applicable")
                checks.append({"expect": "options.outputs rule", "ok": bool(ok), "observed": observed})
            row["spec_checks"] = checks
            row["spec_checks_pass"] = all(x["ok"] for x in checks)
            if imp["spec"] is None:
                row.update({"pass": False, "error": "import produced no spec: " + imp["stderr"].strip()[-200:]})
                rows.append(row)
                continue
            blocks, _ = c["hand"](imp["spec"])
            outputs = getpath(imp["spec"], "options.outputs")[1]
            outs_hand = outputs
            need = {"fission": [], "photon": ["photons"], "damage": ["damage"]}[c["kind"]]
            adjusted = False
            if isinstance(outputs, list):
                add = [n for n in need if n not in outputs]
                if add:
                    outputs, adjusted = outputs + add, True      # the importer's own list lacks what the run needs
                outs_hand = outputs
            imported_for_run = imp["out"] if not adjusted else hand_spec(imp["spec"], {}, outputs)
            hand = hand_spec(imp["spec"], blocks, outs_hand if adjusted else "keep")
            res_imp = run.run_spec(f"g4_{c['id']}_imported", cand, imported_for_run)
            res_hand = run.run_spec(f"g4_{c['id']}_hand", cand, hand)
            row["outputs_extended_by_checker"] = adjusted
            if res_imp is None or res_hand is None:
                row.update({"pass": False, "error": "a run failed: " + str(
                    {k: v.get("error") for k, v in run.runs.items() if k.startswith(f"g4_{c['id']}") and v.get("error")})})
                rows.append(row)
                continue
            equal = canonical(normalize_result(res_imp)) == canonical(normalize_result(res_hand))
            row["result_identical_apart_from_ms"] = equal
            if not equal:
                row["first_differences"] = json_diffs(normalize_result(res_imp), normalize_result(res_hand), limit=10)
            steps = res_imp.get("steps") or []
            if c["kind"] == "fission":
                row["non_vacuous"] = has_fission_products(res_imp)
            elif c["kind"] == "photon":
                row["non_vacuous"] = any("photon_source" in s and s["photon_source"] for s in steps)
            else:
                row["non_vacuous"] = any("damage" in s and s["damage"] for s in steps)
            row["pass"] = bool(row["spec_checks_pass"] and equal and row["non_vacuous"])
        except (KeyError, TypeError, ValueError, AttributeError, IndexError, OSError) as error:
            row.update({"pass": False, "error": f"{type(error).__name__}: {error}"})
        rows.append(row)
    return {"cases": rows, "pass": bool(rows) and all(r.get("pass") for r in rows),
            "note": "the hand-written spec is the imported spec with the feature blocks replaced by blocks built here "
                    "from the checker's own file hashes"}


# ------------------------------------------------------------------------------ gate G5


def g5_unsupported(ref_report, cand_report):
    """Amendment B1: (item 2-7 keywords unsupported beyond the item-7 keeps, other unsupported entries the reference does
    not also report by (keyword, line), the remaining entries both report)."""
    ref_set = {(e["keyword"], e["line"]) for e in entries_of(ref_report, status="unsupported")}
    item, new, remaining = [], [], []
    for e in entries_of(cand_report, status="unsupported"):
        key = (e["keyword"], e["line"])
        if e["keyword"] in ITEM_KEYWORDS and e["keyword"] not in RETAINED_UNSUPPORTED:
            item.append(f"{e['keyword']}@{e['line']}")
        elif key not in ref_set:
            new.append(f"{e['keyword']}@{e['line']}")
        else:
            remaining.append(f"{e['keyword']}@{e['line']}")
    return item, new, remaining


def gate_g5(run, ref, cand, fx, inputs):
    out = {"conderc": {}, "pypact": {}}
    u235 = "nfy-092_U_235.endf"
    for name in ("U2352E4s.i", "U235pulse.i"):
        deck = inputs["conderc_root"] / name
        extra = ["--fluxes", str(inputs["conderc_root"] / "fluxes_therm")]
        a = run.import_deck(ref, deck, run.work / "g5" / "conderc_ref" / f"{name}.json", extra)
        b = run.import_deck(cand, deck, run.work / "g5" / "conderc" / f"{name}.json", extra + fx.yield_args(u235))
        row = {"exit_ref": a["exit"], "exit_cand": b["exit"], "unsupported_ref": unsupported_keywords(a["report"]),
               "unsupported_cand": unsupported_keywords(b["report"])}
        item, new, remaining = g5_unsupported(a["report"], b["report"])
        row["unsupported_item_keywords"], row["unsupported_beyond_reference"] = item, new
        row["unsupported_remaining_also_in_reference"] = remaining
        row["files_in_spec_match"] = C_files("fission_yields.files", [(fx.yields[u235]["path"], fx.yields[u235]["sha256"])]
                                             ).fn(b, None)[0] if b["spec"] else False
        row["import_ok"] = a["exit"] == 0 and b["exit"] == 0 and b["spec"] is not None
        row["import_pass"] = bool(row["import_ok"] and not item and not new and row["files_in_spec_match"])
        if row["import_pass"]:
            res = run.run_spec(f"g5_conderc_{name}", cand, b["out"])
            row["run_ok"] = res is not None
            if res is None:
                row["run_error"] = run.runs.get(f"g5_conderc_{name}", {}).get("error")
        else:
            row["run_ok"] = False
        row["pass"] = bool(row["import_pass"] and row["run_ok"])
        out["conderc"][name] = row
    for rel in sorted(PYPACT_PINS):
        tag = rel.replace("/", "__")
        deck = inputs["pypact_root"] / rel
        proton = re.search(r"^\s*PROJECTILE\s+([2-7])\b", deck.read_text(errors="replace"), re.M | re.I)
        flux = fx.flux162 if proton else fx.flux709
        a = run.import_deck(ref, deck, run.work / "g5" / "pypact_ref" / f"{tag}.json", ["--fluxes", str(flux)])
        b = run.import_deck(cand, deck, run.work / "g5" / "pypact" / f"{tag}.json",
                            ["--fluxes", str(flux), "--files", str(fx.files_ggbins)])
        item, new, remaining = g5_unsupported(a["report"], b["report"])
        bad = item + new
        out["pypact"][tag] = {"exit": b["exit"], "exit_ref": a["exit"], "unsupported": unsupported_keywords(b["report"]),
                              "unsupported_outside_item_7_keeps": bad,
                              "unsupported_remaining_also_in_reference": remaining,
                              "pass": bool(b["exit"] == 0 and a["exit"] == 0 and b["spec"] is not None and not bad),
                              "stderr": b["stderr"].strip()[-200:]}
    out["pass"] = all(v["pass"] for v in out["conderc"].values()) and all(v["pass"] for v in out["pypact"].values()) \
        and len(out["pypact"]) == len(PYPACT_PINS)
    return out


# ------------------------------------------------------------------------------ gate G7, G0


NEW_OPTIONS = ("--fission-yields", "--damage", "--self-shielding-table", "--resonance")
HANDBOOK_KEYWORDS = ("USEFISSION", "NOFISS", "CUMFYLD", "FISYIELD", "FISCHOOSE", "NOXRAY", "SPEK", "READGG",
                     "ALLDISPEN", "ATDISPEN", "UNCTYPE", "SENSITIVITY", "SSFGEOMETRY", "PROBTABLE", "SSFDILUTION",
                     "XSTHRESHOLD")


def grep_terms(path, terms):
    text = Path(path).read_text(errors="replace") if Path(path).is_file() else ""
    return {t: (t in text) for t in terms}, bool(text)


def gate_g7(run, cand, skip_docs):
    scorecard, sc_exists = grep_terms(SCORECARD, NEW_OPTIONS)
    handbook, hb_exists = grep_terms(HANDBOOK, NEW_OPTIONS + HANDBOOK_KEYWORDS)
    words = {w: (w in HANDBOOK.read_text(errors="replace")) if HANDBOOK.is_file() else False
             for w in ("mapped", "approximated", "noted", "unsupported")}
    out = {"scorecard_file": str(SCORECARD), "scorecard_options": scorecard, "handbook_file": str(HANDBOOK),
           "handbook_terms": handbook, "handbook_status_words": words}
    out["grep_pass"] = bool(sc_exists and hb_exists and all(scorecard.values()) and all(handbook.values())
                            and all(words.values()))
    if skip_docs:
        out["docs_checker"] = {"skipped": True}
        out["pass"] = False
        out["note"] = "--skip-docs given: the handbook walk-through was not run, so G7 is not passed"
        return out
    docs_out = run.work / "p140_docs" / "result.json"
    code, so, se = run_cmd([sys.executable, DOCS_CHECKER, "--candidate", cand, "--workdir", run.work / "p140_docs",
                            "--out", docs_out, "--timeout", "600"], max(run.timeout, 3600), run.cwd, run.env)
    out["docs_checker"] = {"exit": code, "stdout_tail": so.decode(errors="replace")[-400:],
                           "stderr_tail": se.decode(errors="replace")[-400:], "result": str(docs_out)}
    out["pass"] = bool(out["grep_pass"] and code == 0)
    return out


def gate_g0():
    lines = HASH_LOG.read_text(encoding="utf-8").splitlines()
    out, ok = {}, True
    for label, path in (("protocol", PROTOCOL), ("amendment_a", AMENDMENT), ("amendment_b", AMENDMENT_B)):
        if not path.is_file():
            out[label] = {"path": str(path), "present": False, "in_protocol_hash_txt": False}
            ok = False
            continue
        digest = sha256_file(path)
        hit = any(ln.startswith(digest) and ln.split()[-1] == f"protocols/{path.name}" for ln in lines)
        out[label] = {"path": str(path), "sha256": digest, "in_protocol_hash_txt": hit}
        ok = ok and hit
    out["protocol_sha256"] = out["protocol"].get("sha256")
    out["pass"] = ok
    return out


# ------------------------------------------------------------------------------ damage table

def load_damage_table(path, expected_sha=None):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MissingInput(f"damage table {path} is absent")
    digest = sha256_file(path)
    if expected_sha and digest != expected_sha:
        raise MissingInput(f"damage table {path} has sha256 {digest}, not the pinned {expected_sha}")
    table = json.loads(path.read_text())
    if table.get("format") != "actinv-damage-table-2":
        raise MissingInput(f"damage table {path} is not actinv-damage-table-2 (no recorded displacement energies)")
    recorded = (table.get("displacement") or {}).get("displacement_energy_eV")
    if not isinstance(recorded, dict) or not recorded:
        raise MissingInput(f"damage table {path} records no displacement.displacement_energy_eV")
    elements = sorted(recorded)
    first = "Fe" if "Fe" in recorded else elements[0]
    second = next((e for e in elements if e != first), None)
    return {"path": str(path), "sha256": digest, "recorded": {k: float(v) for k, v in recorded.items()},
            "first": first, "second": second, "elements": elements}


# ------------------------------------------------------------------------------ self-check


def self_check():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # deck writer: structure, order of phases, keywords where the manual puts them
        deck = make_deck("t", library=["CUMFYLD"], library_late=["ALLDISPEN 30.0"], initial=["USEFISSION"],
                         material=FUEL_MATERIAL)
        lines = deck.splitlines()
        assert lines[0] == "CLOBBER" and lines[-1] == "* end" and lines[-2] == "END"
        assert lines.index("CUMFYLD") < lines.index("GETXS 1 709") < lines.index("GETDECAY 1") \
            < lines.index("ALLDISPEN 30.0") < lines.index("FISPACT") < lines.index("USEFISSION") \
            < lines.index("FLUX 1.0E+12")
        assert "* t" == lines[lines.index("FISPACT") + 1]
        assert make_deck("t", schedule=["FLUX 1.0E+10"]).count("FLUX") == 1
        # flux, ggbins and shield-table writers
        txt = flux_file_text(709).splitlines()
        assert len(txt) == 119 + 2 and len(txt[0].split()) == 6 and len(txt[118].split()) == 1
        assert ggbins_text([1.0, 10.0, 100.0]).splitlines()[0] == "2" and len(ggbins_text([1.0, 10.0]).splitlines()) == 3
        bounds = geometric_bounds()
        assert len(bounds) == 710 and bounds[0] == 1e-5 and abs(bounds[-1] / 1e9 - 1.0) < 1e-9
        assert all(b > a for a, b in zip(bounds, bounds[1:]))
        table = json.loads(shield_table_json(bounds, {"W182": (74182, (300, 301))}, (300, 301)))
        assert table["format"] == "actinv-shield-table-1" and len(table["group_structure"]["boundaries_eV"]) == 710
        assert {g["group"] for g in table["nuclides"]["W182"]["groups"]} == {299, 300, 408, 409}
        assert dilution_index(1) == 708 and dilution_index(709) == 0 and dilution_index(300) == 409
        # manifest digest: path-sorted, content-bound
        (tmp / "A").mkdir()
        (tmp / "B").mkdir()
        (tmp / "A" / "x.i").write_text("1")
        (tmp / "B" / "y.i").write_text("2")
        (tmp / "B" / "y_files").write_text("3")
        decks, inputs = fns_files(tmp)
        assert [p.name for p in decks] == ["x.i", "y.i"] and len(inputs) == 3
        d1 = manifest_digest(tmp, decks)
        (tmp / "B" / "y.i").write_text("22")
        assert manifest_digest(tmp, decks) != d1
        # nuclide names and yield-file names
        assert nuclide_key("U235") == nuclide_key("U-235") == nuclide_key("u235") == ("U", 235, 0)
        assert nuclide_key("Am242m") == nuclide_key("AM242M1") == ("AM", 242, 1) and nuclide_key("Fe") is None
        assert yield_file_parent("nfy-094_Pu_239.endf") == ("PU", 239, 0)
        assert yield_file_parent("nfy-095_Am_242m1.endf") == ("AM", 242, 1)
        assert yield_set("nfy-092_U_235.endf", "nfy-092_U_238.endf") == {("U", 235, 0), ("U", 238, 0)}
        # normalisation drops ms (top level only) and the mesh timing keys, nothing else
        res = {"ms": 5, "steps": [{"ms": 1, "wall_time_s": 2, "x": 1}], "wall_time_s": 3}
        assert normalize_result(res) == {"steps": [{"ms": 1, "x": 1}]}
        assert canonical(normalize_result({"ms": 1, "a": 2})) == canonical(normalize_result({"ms": 9, "a": 2}))
        assert canonical(normalize_result({"ms": 1, "a": 2})) != canonical(normalize_result({"ms": 1, "a": 3}))
        # json diffs and the attribution of spec differences
        a = {"photon": {}, "fission_yields": {"files": []}, "options": {"temperature_K": 1.0}}
        b = {"photon": {"missing_spectra": "estimate", "exclude_xrays": True},
             "fission_yields": {"files": [{"path": "p", "sha256": "s"}], "yields": "cumulative"},
             "options": {"temperature_K": 1.0, "outputs": ["photons"]}, "overrides": [1]}
        diffs = json_diffs(a, b)
        paths = {d["path"] for d in diffs}
        assert {"photon.missing_spectra", "photon.exclude_xrays", "fission_yields.files", "fission_yields.yields",
                "options.outputs", "overrides"} <= paths, paths
        assert json_diffs({"a": 1}, {"a": 1}) == [] and json_diffs({"a": 1}, {"a": 1.0}) != []
        assert spec_owners("fission_yields.files[0].path") == frozenset()
        assert spec_owners("fission_yields.yields") == {"CUMFYLD", "FISYIELD", "NOFISS"}
        assert spec_owners("photon.missing_spectra") == {"SPEK"} and spec_owners("damage.table.sha256") is None
        assert spec_owners("self_shielding.geometry") is None and spec_owners("pathways.targets") is None
        attr = attribute([], diffs, {"SPEK", "NOXRAY", "CUMFYLD"})
        by = {d["path"]: d for d in attr}
        assert by["photon.missing_spectra"]["attributed"] and by["photon.exclude_xrays"]["attributed_to"] == ["NOXRAY"]
        assert by["options.outputs"]["attributed_to"] == ["NOXRAY", "SPEK"]
        assert not by["fission_yields.files"]["attributed"] and not by["overrides"]["attributed"]
        assert by["fission_yields.yields"]["attributed_to"] == ["CUMFYLD"]
        # report comparison: status change attributed to its keyword, an unknown keyword's change is not
        def report(spek, usef, path_detail="p"):
            es = [{"keyword": "SPEK", "line": 3, "phase": "library", "status": spek, "detail": "d", "args": ""},
                  {"keyword": "USEFISSION", "line": 8, "phase": "initial", "status": usef, "detail": "x", "args": ""},
                  {"keyword": "PATH", "line": 9, "phase": "initial", "status": "approximated", "detail": path_detail,
                   "args": ""}]
            return {"schema": REPORT_SCHEMA, "entries": es, "counts": tally({"entries": es}), "steps": 3}
        rd = diff_reports(report("noted", "unsupported"), report("approximated", "unsupported"))
        assert [(d["keyword"], d["what"]) for d in rd] == [("SPEK", "status")]
        assert attribute(rd, [], set())[0]["attributed"]
        rd2 = diff_reports(report("noted", "unsupported"), report("noted", "unsupported", "changed"))
        assert rd2[0]["keyword"] == "PATH" and not attribute(rd2, [], set())[0]["attributed"]
        bad = report("noted", "unsupported")
        bad["counts"] = {"mapped": 0, "approximated": 0, "noted": 0, "unsupported": 0}
        assert any(d["what"].startswith("counts") for d in diff_reports(report("noted", "unsupported"), bad))
        assert status_rows(report("noted", "unsupported"))[0] == ("SPEK", 3, "noted")
        assert unsupported_keywords(report("noted", "unsupported")) == ["USEFISSION"]
        # checks against synthetic imports
        spec = {"fission_yields": {"files": [{"path": str(tmp / "A" / "x.i"), "sha256": sha256_file(tmp / "A" / "x.i")}],
                                   "yields": "cumulative", "parents": {"include": ["U235", "Pu-239"]}},
                "photon": {"missing_spectra": "estimate", "exclude_xrays": True,
                           "group_boundaries_eV": GGBINS_GOOD},
                "uncertainty": {"covariance": {"path": "c", "sha256": "0" * 64}, "channels": ["decay_constants"],
                                "voi": {"top": 10}},
                "overrides": [{"nuclide": "Fe56", "kind": "cross_section_uncertainty", "mt": 103, "value": 0.15}],
                "damage": {"table": {"path": str(tmp / "A" / "x.i"), "sha256": sha256_file(tmp / "A" / "x.i")},
                           "displacement_energy_eV": {"Fe": 40.0}},
                "self_shielding": {"geometry": {"shape": "cylinder", "length_cm": 0.5, "height_cm": 2.0,
                                                "density_g_cm3": 19.3, "resonance_files": []},
                                   "dilution_overrides": {"W182": {"409": 100.0}}},
                "options": {"outputs": ["activity", "photons"]}}
        rep = report("approximated", "mapped")
        rep["entries"][0]["detail"] = "estimate with certified dose bounds; photons added to options.outputs"
        imp = {"exit": 0, "stderr": "", "spec": spec, "report": rep}
        pairs = [(str(tmp / "A" / "x.i"), sha256_file(tmp / "A" / "x.i"))]
        good = [C_exit0(), C_status("SPEK", ["approximated"]), C_detail("SPEK", ["bound", "x"], mode="any"),
                C_eq("photon.missing_spectra", "estimate"), C_eq("photon.group_boundaries_eV", GGBINS_GOOD),
                C_absent("photon.nothing"), C_present("uncertainty.voi"), C_channels({"decay_constants"}),
                C_parents("include", {("U", 235, 0), ("PU", 239, 0)}), C_files("fission_yields.files", pairs),
                C_override("Fe56", "cross_section_uncertainty", 103, 0.15), C_no_override("cross_section"),
                C_damage(pairs[0][0], pairs[0][1], {"Fe": 40.0}),
                C_geometry("cylinder", 0.5, 2.0, 19.3), C_status("USEFISSION", ["mapped"]),
                C_no_unsupported(["PATH"]), C_vs_base(["photon", "fission_yields", "uncertainty", "overrides",
                                                       "damage", "self_shielding", "options"])]
        base = {"exit": 0, "spec": {"options": {"outputs": ["activity"]}}, "report": rep}
        for chk in good:
            ok, obs = chk.fn(imp, base)
            assert ok, (chk.desc, obs)
        assert C_outputs_photons(["SPEK"]).fn(imp, base)[0]
        assert not C_outputs_photons(["NOXRAY"]).fn(imp, base)[0]          # NOXRAY's detail does not say photons
        bad_checks = [C_status("SPEK", ["noted"]), C_eq("photon.exclude_xrays", False), C_absent("photon"),
                      C_channels({"cross_section_mf33"}), C_parents("exclude", {("U", 235, 0), ("PU", 239, 0)}),
                      C_parents("include", {("U", 235, 0)}), C_files("fission_yields.files", [("x", "0" * 64)]),
                      C_override("Fe56", "cross_section_uncertainty", 102, 0.15),
                      C_damage(pairs[0][0], "0" * 64, {"Fe": 40.0}), C_damage(pairs[0][0], pairs[0][1], {"Fe": 41.0}),
                      C_geometry("foil", 0.5), C_names("fission_yields.parents.include", set())]
        for chk in bad_checks:
            assert not chk.fn(imp, base)[0], chk.desc
        assert C_channels({"cross_section_mf33"}).fn({"spec": {"uncertainty": {"covariance": {}}}}, None)[0]
        assert not C_channels({"cross_section_mf33", "decay_constants"}).fn({"spec": {"uncertainty": {}}}, None)[0]
        assert same(0.1 + 0.2, 0.3) and not same(1.0, 1.1) and same({"a": [1, 2.0]}, {"a": [1, 2.0]})
        assert not same(True, 1) and same("a", "a")
        # a refusal reason is found whatever its wording around the option name
        rep2 = {"entries": [{"keyword": "USEFISSION", "line": 1, "phase": "initial", "status": "unsupported",
                             "detail": "pass --fission-yields FILE to name the data", "args": ""}]}
        assert "--fission-yields" in detail_text(rep2, "USEFISSION", "unsupported")
        assert not detail_text(rep2, "USEFISSION", "mapped")
        assert multi("--resonance", ["a", "b"], "space") == ["--resonance", "a", "b"]
        assert multi("--resonance", ["a", "b"], "repeat") == ["--resonance", "a", "--resonance", "b"]
        assert report_path_of("x/out.json") == "x/out.import.json" and report_path_of("out") == "out.import.json"
        # hand-written spec builder and ordering rule
        h = hand_spec({"a": 1, "options": {"outputs": ["x"]}, "fission_yields": {"files": []}},
                      {"fission_yields": {"files": [1]}, "a": None}, ["x", "photons"])
        assert h == {"options": {"outputs": ["x", "photons"]}, "fission_yields": {"files": [1]}}
        assert ordered_like(["Pu239", "U235"], ["U235", "PU239"]) == ["Pu239", "U235"]
        assert ordered_like(["U235"], ["U235", "U238"]) == ["U235", "U238"]
        assert has_fission_products({"steps": [{"inventory": [{"nuclide": "Xe135"}]}]})
        assert not has_fission_products({"steps": []})
        # the case catalogues build offline from placeholder fixtures
        yields = {}
        for name in YIELD_PINS:
            p = write_text(tmp / "y" / name, name)
            yields[name] = {"path": str(p), "sha256": sha256_file(p)}
        damage = {"path": str(write_text(tmp / "damage.json", "{}")), "sha256": "ab" * 32,
                  "recorded": {"Fe": 40.0, "Cr": 40.0}, "first": "Fe", "second": "Cr", "elements": ["Cr", "Fe"],
                  "mismatch": {"path": str(tmp / "damage.json"), "sha256": "cd" * 32}}
        fx = Fixtures(tmp / "work", yields, damage, "space")
        g3 = build_g3_cases(fx)
        ids = [c.id for c in g3]
        assert len(ids) == len(set(ids)) and len(g3) >= 50, len(g3)
        for needed in ("damage_all_then_at", "damage_at_then_all", "fission_fisyield_positive",
                       "fission_fisyield_negative", "fission_fisyield_zero", "uncertainty_unctype_1",
                       "uncertainty_unctype_3", "selfshield_ssfdilution", "kept_readsf", "correction_xsthreshold"):
            assert needed in ids, needed
        assert all("FISPACT" in c.deck and c.deck.rstrip().endswith("* end") or c.id.startswith("info_")
                   for c in g3)
        dd = next(c for c in g3 if c.id == "damage_all_then_at").deck
        assert dd.index("ALLDISPEN") < dd.index("ATDISPEN")
        dd = next(c for c in g3 if c.id == "damage_at_then_all").deck
        assert dd.index("ATDISPEN") < dd.index("ALLDISPEN")
        assert "FISYIELD -1 U238" in next(c for c in g3 if c.id == "fission_fisyield_negative").deck
        assert "FISYIELD 0" in next(c for c in g3 if c.id == "fission_fisyield_zero").deck
        assert all(c.args[:2] == ["--fluxes", str(fx.flux709)] for c in g3)
        g6 = build_g6_cases(fx)
        assert len(g6) >= 19 and len({c["id"] for c in g6}) == len(g6)
        assert all(c["terms"] and c["keyword"] for c in g6)
        assert len(g4_cases(fx)) == 5
        assert fx.bad["junk"].is_file() and fx.table.is_file() and len(fx.resonance) == 2
        names = [p.name for p in (tmp / "work" / "fixtures").iterdir()]
        assert not any(n.endswith((".endf", ".npz", ".zip")) for n in names), names
        # the protocol and gate bookkeeping run offline
        assert gate_g0()["protocol_sha256"] == sha256_file(PROTOCOL)
        assert "amendment_a" in gate_g0()
    print("P155 self-check: PASS")
    return 0


def list_expectations():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        yields = {n: {"path": str(write_text(tmp / "y" / n, n)), "sha256": "0" * 64} for n in YIELD_PINS}
        damage = {"path": str(tmp / "d.json"), "sha256": "0" * 64, "recorded": {"Fe": 40.0, "Cr": 40.0},
                  "first": "Fe", "second": "Cr", "elements": ["Cr", "Fe"]}
        fx = Fixtures(tmp / "w", yields, damage, "space")
        for c in build_g3_cases(fx):
            print(f"G3 {c.id}{'' if c.gating else ' (information)'}")
            for chk in c.checks:
                print(f"     {chk.desc}")
        for c in build_g6_cases(fx):
            print(f"G6 {c['id']}: {c['keyword']} unsupported, detail names {c['terms']}, empty {c['forbid']}, "
                  f"--strict exits nonzero and writes no spec")
    return 0


# ------------------------------------------------------------------------------ main


def find_data_root(data_dir):
    candidate = Path(data_dir).expanduser()
    if candidate.is_absolute():
        return candidate
    for base in [ROOT] + list(ROOT.parents):
        if (base / candidate).is_dir():
            return base / candidate
    raise MissingInput(f"data directory {data_dir} not found under {ROOT} or its parents")


def collect_inputs(args):
    """Verify every pinned input and return the paths. Raises MissingInput."""
    inputs = {"hashes": {}}
    fns_root = Path(args.fns).expanduser().resolve()
    decks, every = fns_files(fns_root)
    if len(decks) != EXPECTED_FNS_DECKS:
        raise MissingInput(f"{fns_root}: {len(decks)} FNS decks, expected {EXPECTED_FNS_DECKS}")
    if manifest_digest(fns_root, decks) != FNS_DECKS_DIGEST:
        raise MissingInput(f"{fns_root}: the manifest digest of the decks differs from the pin {FNS_DECKS_DIGEST}")
    if manifest_digest(fns_root, every) != FNS_INPUTS_DIGEST:
        raise MissingInput(f"{fns_root}: the digest of decks, files files and fluxes files differs from the pin "
                           f"{FNS_INPUTS_DIGEST}")
    inputs["fns_root"] = fns_root
    inputs["hashes"]["fns"] = {"decks": FNS_DECKS_DIGEST, "decks_files_fluxes": FNS_INPUTS_DIGEST,
                               "n_decks": len(decks), "n_files": len(every)}
    pypact = Path(args.pypact).expanduser().resolve()
    inputs["pypact_root"] = pypact
    inputs["hashes"]["pypact"] = {rel: require_pinned(pypact / rel, sha, f"pypact {rel}")["sha256"]
                                  for rel, sha in PYPACT_PINS.items()}
    conderc = Path(args.conderc).expanduser().resolve()
    inputs["conderc_root"] = conderc
    inputs["hashes"]["conderc"] = {rel: require_pinned(conderc / rel, sha, f"CONDERC {rel}")["sha256"]
                                   for rel, sha in CONDERC_PINS.items()}
    nfpy = Path(args.nfpy_dir).expanduser().resolve()
    inputs["yields"] = {name: require_pinned(nfpy / name, sha, f"yield file {name}") for name, sha in YIELD_PINS.items()}
    data_dir = find_data_root(args.data_dir)
    inputs["library"] = require_pinned(data_dir / "activation" / LIBRARY_NAME, LIBRARY_SHA256, "TENDL-2025 library")
    inputs["damage"] = load_damage_table(args.damage_table, args.damage_table_sha256)
    if args.damage_table_mismatch:
        mm = load_damage_table(args.damage_table_mismatch)
        el = inputs["damage"]["first"]
        if el not in mm["recorded"] or mm["recorded"][el] == inputs["damage"]["recorded"][el]:
            raise MissingInput(f"--damage-table-mismatch must record a different displacement energy for {el}")
        inputs["damage"]["mismatch"] = {"path": mm["path"], "sha256": mm["sha256"]}
    return inputs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--workdir", default="target/p155-work")
    ap.add_argument("--out", default="results/p155_importer_sweep.json")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds allowed per `run`")
    ap.add_argument("--import-timeout", type=int, default=300, help="seconds allowed per import")
    ap.add_argument("--fns", default="~/nuclear-data/conderc-fns/fns")
    ap.add_argument("--pypact", default="~/nuclear-data/ext-decks/pypact")
    ap.add_argument("--conderc", default="~/nuclear-data/conderc-fission/fission/Fispact-II/Inputs")
    ap.add_argument("--nfpy-dir", default="~/nuclear-data/endfb-viii.0-nfpy")
    ap.add_argument("--data-dir", default="actinv-data/v1.1.0")
    ap.add_argument("--damage-table", help="an actinv-damage-table-2 file from `actinv build-damage --method kinematic`")
    ap.add_argument("--damage-table-sha256", help="pin for the damage table (recorded when absent)")
    ap.add_argument("--damage-table-mismatch", help="a second table recording another displacement energy")
    ap.add_argument("--file-list-style", choices=("space", "repeat"), default="space")
    ap.add_argument("--skip-docs", action="store_true", help="do not run check_p140_docs.py (G7 then cannot pass)")
    ap.add_argument("--reuse", action="store_true", help="reuse existing `run` outputs in the workdir")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--list-expectations", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if args.list_expectations:
        return list_expectations()
    for name in ("reference", "candidate", "damage_table"):
        if not getattr(args, name):
            ap.error(f"--{name.replace('_', '-')} is required")
    ref, cand = Path(args.reference).resolve(), Path(args.candidate).resolve()
    work = Path(args.workdir)
    work = (work if work.is_absolute() else ROOT / work).resolve()
    out_path = Path(args.out)
    out_path = out_path if out_path.is_absolute() else ROOT / out_path
    try:
        for label, binary in (("reference", ref), ("candidate", cand)):
            if not binary.is_file():
                raise MissingInput(f"{label} binary {binary} is absent")
        ref_sha = sha256_file(ref)
        if ref_sha != REFERENCE_SHA256:
            raise MissingInput(f"reference binary has sha256 {ref_sha}, not the pinned v1.6.1 {REFERENCE_SHA256}")
        inputs = collect_inputs(args)
    except MissingInput as error:
        print(f"P155: missing or mismatching input: {error}", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    run = Runner(work, args.timeout, args.import_timeout, args.reuse, ROOT)
    fx = Fixtures(work, inputs["yields"], inputs["damage"], args.file_list_style)
    inputs["flux709"], inputs["flux162"] = fx.flux709, fx.flux162
    record = {"protocol": "protocols/ACTINV-P155_PROTOCOL.md", "protocol_sha256": sha256_file(PROTOCOL),
              "checker_sha256": sha256_file(__file__), "reference_sha256": ref_sha, "candidate_sha256": sha256_file(cand),
              "pinned_inputs": {**inputs["hashes"], "yield_files": inputs["yields"], "library": inputs["library"],
                                "damage_table": {k: inputs["damage"][k] for k in ("path", "sha256", "recorded", "first",
                                                                                   "second")},
                                "damage_table_mismatch": inputs["damage"].get("mismatch")},
              "synthetic_files": {"fluxes_709": sha256_file(fx.flux709), "fluxes_162": sha256_file(fx.flux162),
                                  "ggbins_good": sha256_file(fx.ggbins), "bondarenko_placeholder": sha256_file(fx.table),
                                  "resonance_placeholders": [sha256_file(p) for p in fx.resonance]},
              "assumptions": [ln.strip() for ln in __doc__.split("ASSUMPTIONS")[1].split("Usage:")[0].splitlines()
                              if ln.strip().startswith("A")],
              "external_gates": {"G1": "fmt, clippy -D warnings, workspace tests: coordinator",
                                 "G8": "CI on the pushed commit and committed baselines: coordinator"},
              "gates": {}}
    gates = record["gates"]
    gates["G0"] = gate_g0()

    def guarded(name, fn):
        try:
            gates[name] = fn()
        except (RuntimeError, KeyError, ValueError, IndexError, TypeError, OSError, AttributeError) as error:
            gates[name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        print(f"P155 {name}: {'PASS' if gates[name].get('pass') else 'FAIL'}"
              + (f" ERROR {gates[name]['error']}" if "error" in gates[name] else ""))
        sys.stdout.flush()
    guarded("G2", lambda: gate_g2(run, ref, cand, inputs))
    guarded("G3", lambda: gate_g3(run, cand, fx))
    guarded("G4", lambda: gate_g4(run, cand, fx))
    guarded("G5", lambda: gate_g5(run, ref, cand, fx, inputs))
    guarded("G6", lambda: gate_g6(run, ref, cand, fx))
    guarded("G7", lambda: gate_g7(run, cand, args.skip_docs))
    record["runs"] = run.runs
    judged = ("G0", "G2", "G3", "G4", "G5", "G6", "G7")
    overall = all(gates[g].get("pass") is True for g in judged)
    record["verdict"] = "PASS" if overall else "FAIL"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
    for g in ("G3", "G6"):
        for r in (gates[g].get("cases") or []):
            if r.get("gating", True) and not r.get("pass"):
                print(f"  {g}/{r['id']}: FAIL")
                for c in r.get("checks") or []:
                    if not c["ok"]:
                        print(f"      {c['expect']} -- {str(c['observed'])[:160]}")
    for r in (gates["G4"].get("cases") or []):
        if not r.get("pass"):
            print(f"  G4/{r['id']}: FAIL {r.get('error', '')}")
    print("P155 G1, G8: external (not evaluated here)")
    print(f"P155: {record['verdict']} ({out_path})")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
