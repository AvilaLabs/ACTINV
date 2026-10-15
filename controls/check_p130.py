#!/usr/bin/env python3
"""P130 gates G2 to G5 (protocols/ACTINV-P130_PROTOCOL.md).

  G2  All 13 vendored FISPACT-II group files equal the boundaries parsed from ebins_N inside FISPACT's own
      ebins.tar.bz2 (archive SHA-256 verified against the value recorded in the vendored JSON).
  G3  Unchanged by default: examples/fns_fe_5min.json gives byte-identical results (apart from `ms`) under reference
      and candidate, and a small one-element TENDL-2025 709-group neutron library builds to identical NPZ arrays and
      an index that differs only in `builder_fingerprint`.
  G4  Splice: a synthetic residual table spliced into that library leaves every row below the effective splice
      byte-identical, replaced groups equal the table, added rows are zero below the splice, and the index records
      the requested and effective splice and the table SHA-256. Run at 200 MeV (exact on the grid) and 205 MeV
      (snaps up to the next boundary); a table that does not match the effective splice must be refused.
  G5  Report only: an equal-lethargy spectrum on fispact-1102 and the same physical spectrum on fispact-709, each
      against a library built on its own structure (candidate binary). Compares the one-group reaction rates and the
      total activity after a one-step irradiation, and reports the ratios against a stated tolerance.

The verdict is PASS only if G2, G3 and G4 pass.

Usage:
  check_p130.py --reference REF_BIN --candidate CAND_BIN [--workdir target/p130-work] [--out results/p130_groups.json]

Run inside the required systemd cgroup with a disk-backed TMPDIR. Every subprocess is launched without a shell, with
a timeout, one at a time. Needs numpy.

Order of boundaries. FISPACT's ebins_N lists the N+1 group boundaries as Fortran reals, highest energy first
(line 1: a quoted title, line 2: N, then comma-separated boundaries). The vendored JSON files keep that published
order (`order_as_published: descending`). The comparison is therefore element by element in descending order, as
Python floats parsed from the same decimal text; the library loader reverses to ascending after reading.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

try:
    import numpy as np
except ImportError:  # reported as a gate error, not a crash
    np = None

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "crates/actinv-data/data"
PROTOCOL = ROOT / "protocols/ACTINV-P130_PROTOCOL.md"
STRUCTURES = [66, 69, 100, 162, 172, 175, 211, 315, 351, 586, 616, 709, 1102]
ARCHIVE = Path.home() / "nuclear-data/fispact-tendl2017/ebins.tar.bz2"
TENDL_DIR = Path.home() / "nuclear-data/tendl-2025/files/n"
FNS_SPEC = ROOT / "examples/fns_fe_5min.json"
DECAY_PRIMARY = ROOT / "actinv-data/v1.1.0/decay/endf-b-viii-0_decay.dat"
DECAY_FALLBACK = ROOT / "actinv-data/v1.1.0/decay/jeff-3-3_decay.dat"

RUN_TIMEOUT_S = 1800
BUILD_TIMEOUT_S = 3600
BUILD_ARGS = ["--format", "tendl", "--projectile", "neutron", "--temperature-K", "293.6", "--workers", "1"]

# G5 spectrum: equal flux per unit lethargy between two boundaries that both structures contain.
G5_E_LO_EV = 1.0e-5
G5_E_HI_EV = 2.0e7
G5_TOTAL_FLUX = 1.0e14
G5_IRRADIATION = "3600 s"
G5_TOLERANCE_ACTIVITY = 0.01
G5_TOLERANCE_RATE = 0.02
G5_SIGNIFICANT_RATE_FRACTION = 1e-3

G5_TOLERANCE_TEXT = (
    "The test spectrum is flat in lethargy and the library collapses each group with flat-lethargy weighting, so "
    "the group-summed rate sum_g sigma_g * phi_g approximates the same integral of sigma over lethargy on both "
    "structures. Both spectrum edges (10 micro-eV and 20 MeV) are boundaries of both structures, so no group is "
    "split. What remains is the group-collapse difference: threshold onsets, resonance peaks and the Doppler "
    "reconstruction fall differently inside 709 and 1102 groups, and the activity adds a few chained reactions. "
    f"The stated tolerances are {G5_TOLERANCE_ACTIVITY:.0%} on the total activity and {G5_TOLERANCE_RATE:.0%} on "
    f"each reaction rate above {G5_SIGNIFICANT_RATE_FRACTION:g} of the largest rate; this is report-only and does "
    "not enter the verdict."
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True)


def first_differences(a, b, path="", limit=10, out=None):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in only one")
            else:
                first_differences(a[k], b[k], f"{path}/{k}", limit, out)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            first_differences(x, y, f"{path}[{i}]", limit, out)
            if len(out) >= limit:
                break
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


class Runner:
    """Serial, shell-free subprocess runner with logs and timeouts."""

    def __init__(self, workdir):
        self.logs = workdir / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)

    def run(self, argv, tag, timeout, cwd=None):
        log = self.logs / f"{tag}.log"
        start = time.monotonic()
        with open(log, "wb") as handle:
            try:
                proc = subprocess.run([str(a) for a in argv], stdout=handle, stderr=subprocess.STDOUT,
                                      timeout=timeout, cwd=cwd, check=False)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"{tag}: timed out after {timeout} s (log {log})")
        return {"rc": proc.returncode, "seconds": round(time.monotonic() - start, 2), "log": str(log),
                "argv": [str(a) for a in argv]}


# ------------------------------------------------------------------------------------------------- G2


def parse_ebins(text):
    """Parse a FISPACT ebins_N file: quoted title, group count, then N+1 boundaries, highest energy first."""
    parts = text.split("\n", 2)
    if len(parts) < 3:
        raise ValueError("ebins file has fewer than three lines")
    title = parts[0].strip()
    if not (title.startswith("'") and title.endswith("'")):
        raise ValueError(f"unexpected ebins title line {title!r}")
    groups = int(parts[1].strip())
    tokens = [t for t in re.split(r"[,\s]+", parts[2].strip()) if t]
    values = [float(t.upper().replace("D", "E")) for t in tokens]
    return title.strip("'").strip(), groups, values


def gate_g2(archive):
    out = {"archive": str(archive), "structures": {}}
    archive_sha = sha256_file(archive)
    out["archive_sha256"] = archive_sha
    all_ok = True
    archive_sha_checked = 0
    with tarfile.open(archive, "r:bz2") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        out["archive_members"] = sorted(members)
        for n in STRUCTURES:
            entry = {}
            out["structures"][str(n)] = entry
            try:
                data = json.loads((DATA_DIR / f"fispact_{n}_groups.json").read_text())
                member = members.get(f"ebins_{n}")
                if member is None:
                    raise ValueError(f"ebins_{n} is not in the archive")
                raw = tar.extractfile(member).read()
                source = data.get("source", "")
                file_sha_recorded = re.search(r"ebins_\d+, SHA-256 ([0-9a-f]{64})", source)
                recorded_archive = re.search(r"archive SHA-256 ([0-9a-f]{64})", source)
                entry["source_field"] = source
                entry["ebins_sha256"] = hashlib.sha256(raw).hexdigest()
                if file_sha_recorded:
                    entry["recorded_file_sha256"] = file_sha_recorded.group(1)
                    entry["file_sha256_matches"] = file_sha_recorded.group(1) == entry["ebins_sha256"]
                else:
                    entry["recorded_file_sha256"] = None
                    entry["file_sha256_matches"] = None
                    entry["note"] = "no per-file SHA-256 recorded in the JSON source field"
                if recorded_archive:
                    entry["recorded_archive_sha256"] = recorded_archive.group(1)
                    entry["archive_sha256_matches"] = recorded_archive.group(1) == archive_sha
                    archive_sha_checked += 1
                else:
                    entry["recorded_archive_sha256"] = None
                    entry["archive_sha256_matches"] = None
                title, groups, values = parse_ebins(raw.decode("ascii"))
                json_values = data["boundaries_eV"]
                entry.update({
                    "title": title, "ebins_groups": groups, "ebins_boundaries": len(values),
                    "json_boundaries": len(json_values), "json_n_boundaries_field": data.get("n_boundaries"),
                    "json_order_as_published": data.get("order_as_published"),
                    "ebins_descending": all(a > b for a, b in zip(values, values[1:])),
                    "json_descending": all(a > b for a, b in zip(json_values, json_values[1:])),
                })
                mismatches = [i for i, (a, b) in enumerate(zip(values, json_values)) if a != b]
                entry["mismatch_indices"] = mismatches[:10]
                entry["pass"] = bool(
                    groups == n and len(values) == n + 1 and len(json_values) == n + 1
                    and data.get("n_boundaries") == n + 1 and not mismatches
                    and entry["ebins_descending"] and entry["json_descending"]
                    and entry["file_sha256_matches"] is not False
                    and entry["archive_sha256_matches"] is not False)
            except (ValueError, KeyError, OSError, tarfile.TarError) as error:
                entry["error"] = f"{type(error).__name__}: {error}"
                entry["pass"] = False
            all_ok = all_ok and entry["pass"]
    out["structures_checked"] = len(STRUCTURES)
    out["archive_sha256_checked_against_json"] = archive_sha_checked
    # The 11 structures new in P130 carry both SHA-256 values; require the archive hash to have been compared.
    out["pass"] = bool(all_ok and archive_sha_checked >= 11)
    return out


# ------------------------------------------------------------------------------------------------- libraries


def stage_inputs(src_dir, isotopes, dest):
    """Copy the chosen TENDL files into a clean directory (the builder rejects symlinks)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    files = {}
    for iso in isotopes:
        src = src_dir / f"n-{iso}.tendl"
        if not src.is_file():
            raise FileNotFoundError(f"{src} not found")
        shutil.copyfile(src, dest / src.name)
        files[src.name] = sha256_file(src)
    return files


def index_of(npz):
    return npz.with_name(npz.stem + "_index.json")


def build_library(runner, binary, tag, input_dir, out_npz, groups, decay, extra=()):
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out_npz, index_of(out_npz)):
        if stale.exists():
            stale.unlink()
    argv = [binary, "build-library", input_dir, out_npz, "--groups", groups, *BUILD_ARGS]
    if decay:
        argv += ["--decay", DECAY_PRIMARY, "--decay-fallback", DECAY_FALLBACK]
    argv += list(extra)
    result = runner.run(argv, tag, BUILD_TIMEOUT_S, cwd=ROOT)
    result["npz"] = str(out_npz)
    if result["rc"] == 0:
        if not out_npz.is_file() or not index_of(out_npz).is_file():
            raise RuntimeError(f"{tag}: build exited 0 but wrote no NPZ/index")
        result["npz_sha256"] = sha256_file(out_npz)
    return result


def load_npz(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def arrays_identical(a, b):
    out = {"keys_equal": sorted(a) == sorted(b), "arrays": {}}
    ok = out["keys_equal"]
    for key in sorted(set(a) & set(b)):
        x, y = a[key], b[key]
        same = x.dtype == y.dtype and x.shape == y.shape and x.tobytes() == y.tobytes()
        out["arrays"][key] = {"dtype": str(x.dtype), "shape": list(x.shape), "bitwise_identical": bool(same)}
        ok = ok and same
    out["pass"] = bool(ok)
    return out


# ------------------------------------------------------------------------------------------------- G3


FNS_MS = re.compile(rb'"ms"\s*:\s*[-+0-9.eE]+')


def gate_g3_fns(runner, ref, cand, work):
    out = {}
    raw = {}
    for label, binary in (("reference", ref), ("candidate", cand)):
        dest = work / f"fns_fe_5min.{label}.json"
        if dest.exists():
            dest.unlink()
        result = runner.run([binary, "run", FNS_SPEC, dest], f"g3_fns_{label}", RUN_TIMEOUT_S, cwd=ROOT)
        out[label] = {k: result[k] for k in ("rc", "seconds", "log")}
        if result["rc"] != 0 or not dest.is_file():
            out["pass"] = False
            out["error"] = f"{label} run exited {result['rc']}"
            return out
        raw[label] = dest.read_bytes()
        out[label]["sha256"] = hashlib.sha256(raw[label]).hexdigest()
    masked = {k: FNS_MS.subn(b'"ms": 0', v) for k, v in raw.items()}
    ref_json, cand_json = json.loads(raw["reference"]), json.loads(raw["candidate"])
    ref_json.pop("ms", None)
    cand_json.pop("ms", None)
    out["raw_bytes_identical"] = raw["reference"] == raw["candidate"]
    out["ms_occurrences_masked"] = {k: v[1] for k, v in masked.items()}
    out["bytes_identical_apart_from_ms"] = masked["reference"][0] == masked["candidate"][0]
    out["parsed_equal_without_top_level_ms"] = canonical(ref_json) == canonical(cand_json)
    if not out["parsed_equal_without_top_level_ms"]:
        out["first_differences"] = first_differences(ref_json, cand_json)
    out["pass"] = bool(out["bytes_identical_apart_from_ms"] and out["parsed_equal_without_top_level_ms"])
    return out


def gate_g3_library(ref_build, cand_build):
    out = {"reference_build": {k: ref_build.get(k) for k in ("rc", "seconds", "npz_sha256", "log")},
           "candidate_build": {k: cand_build.get(k) for k in ("rc", "seconds", "npz_sha256", "log")}}
    if ref_build["rc"] != 0 or cand_build["rc"] != 0:
        out["pass"] = False
        out["error"] = "a library build failed; see logs"
        return out
    ref_npz, cand_npz = Path(ref_build["npz"]), Path(cand_build["npz"])
    out["npz"] = arrays_identical(load_npz(ref_npz), load_npz(cand_npz))
    out["npz_bytes_identical"] = ref_build["npz_sha256"] == cand_build["npz_sha256"]
    ref_idx = json.loads(index_of(ref_npz).read_text())
    cand_idx = json.loads(index_of(cand_npz).read_text())
    allowed = ["builder_fingerprint"]
    out["index_keys_allowed_to_differ"] = allowed
    out["builder_fingerprint"] = {"reference": ref_idx.get("builder_fingerprint"),
                                  "candidate": cand_idx.get("builder_fingerprint"),
                                  "differ": ref_idx.get("builder_fingerprint") != cand_idx.get("builder_fingerprint")}
    ref_rest = {k: v for k, v in ref_idx.items() if k not in allowed}
    cand_rest = {k: v for k, v in cand_idx.items() if k not in allowed}
    out["index_equal_apart_from_fingerprint"] = canonical(ref_rest) == canonical(cand_rest)
    if not out["index_equal_apart_from_fingerprint"]:
        out["index_first_differences"] = first_differences(ref_rest, cand_rest)
    out["index_has_spallation_block"] = "spallation" in cand_idx
    out["pass"] = bool(out["npz"]["pass"] and out["index_equal_apart_from_fingerprint"]
                       and not out["index_has_spallation_block"])
    return out


# ------------------------------------------------------------------------------------------------- G4


SYNTHETIC_DESCRIPTION = ("SYNTHETIC TEST DATA for the P130 splice check: invented, non-physical values with no "
                         "evaluation behind them")


def synthetic_values(groups, seed):
    """Dyadic rationals (m / 4096): exact short decimals, so the table's JSON text parses to the same doubles in
    Python and in the Rust loader. One entry is zero to exercise zero handling."""
    values = [((seed * 7 + g * 3) % 61 + 1) / 4096.0 for g in range(groups)]
    values[groups // 2] = 0.0
    return values


def build_table(bounds_asc, first, rows0, targets):
    """Table for a splice whose first spliced boundary index is `first`.

    * library target 0: replaces the existing loss row of one MT (and an existing MF=10 product row if the target
      has one) and adds a new product row to that MT;
    * library target 1: adds new rows under MT 5 (a new loss row and two products);
    * ZA 82208 is not in the library and must be skipped and reported.
    """
    boundaries = [float(b) for b in bounds_asc[first:]]
    groups = len(boundaries) - 1
    t0 = [tuple(int(v) for v in r) for r in rows0 if r[0] == 0]
    loss_mts = [r[1] for r in t0 if r[2] == -1 and r[3] == -1 and r[4] == 0]
    if not loss_mts:
        raise ValueError("library target 0 has no loss row to replace")
    mt_replace = 102 if 102 in loss_mts else loss_mts[0]
    channels = {mt_replace: {"mt": mt_replace, "loss_barns": synthetic_values(groups, 1),
                             "products": [{"zap": 11024, "lfs": 0, "sigma_barns": synthetic_values(groups, 2)}]}}
    mf10 = [r for r in t0 if r[4] == 10]
    if mf10:
        _, mt, zap, lfs, _ = mf10[0]
        channel = channels.setdefault(mt, {"mt": mt, "products": []})
        if not any(p["zap"] == zap and p["lfs"] == lfs for p in channel["products"]):
            channel["products"].append({"zap": zap, "lfs": lfs, "sigma_barns": synthetic_values(groups, 3)})
    table_targets = [{"za": targets[0]["za"], "liso": targets[0]["liso"],
                      "channels": list(channels.values())}]
    if len(targets) > 1:
        table_targets.append({"za": targets[1]["za"], "liso": targets[1]["liso"], "channels": [
            {"mt": 5, "loss_barns": synthetic_values(groups, 4), "products": [
                {"zap": 10021, "lfs": 0, "sigma_barns": synthetic_values(groups, 5)},
                {"zap": 9018, "lfs": 0, "sigma_barns": synthetic_values(groups, 6)}]}]})
    table_targets.append({"za": 82208, "liso": 0, "channels": [
        {"mt": 5, "products": [{"zap": 80200, "lfs": 0, "sigma_barns": synthetic_values(groups, 7)}]}]})
    return {"schema": "actinv-spallation-residuals-1", "description": SYNTHETIC_DESCRIPTION,
            "projectile": "neutron", "boundaries_eV": boundaries, "targets": table_targets}


def effective_index(bounds, splice_ev):
    return next(i for i, b in enumerate(bounds) if b >= splice_ev * (1.0 - 1e-12))


def simulate(rows0, targets, table):
    """Expected rows after the splice, written from the documented semantics.

    Returns (expected_rows, plan) where expected_rows is a list of ('old', index) / ('new', key, sigma) and plan
    holds the replaced sigma per old row, the counts per target and the targets not in the library.
    """
    keys = {(t["za"], t["liso"]): i for i, t in enumerate(targets)}
    existing = {}
    last_of_target = {}
    for idx, r in enumerate(rows0):
        key = tuple(int(v) for v in r)
        existing.setdefault(key, []).append(idx)
        last_of_target[key[0]] = idx
    replaced = {}
    additions = {}
    counts = []
    absent = []
    for tt in table["targets"]:
        t = keys.get((tt["za"], tt["liso"]))
        if t is None:
            absent.append({"za": tt["za"], "liso": tt["liso"]})
            continue
        n_rep = n_add = 0
        for ch in tt["channels"]:
            wanted = []
            if ch.get("loss_barns") is not None:
                wanted.append(((t, ch["mt"], -1, -1, 0), ch["loss_barns"]))
            for p in ch["products"]:
                wanted.append(((t, ch["mt"], p["zap"], p["lfs"], 10), p["sigma_barns"]))
            for key, sigma in wanted:
                if key in existing:
                    for idx in existing[key]:
                        replaced[idx] = sigma
                        n_rep += 1
                else:
                    additions.setdefault(t, []).append((key, sigma))
                    n_add += 1
        counts.append({"za": tt["za"], "liso": tt["liso"], "rows_replaced": n_rep, "rows_added": n_add})
    last_rows = {idx: t for t, idx in last_of_target.items()}
    expected = []
    for idx in range(len(rows0)):
        expected.append(("old", idx))
        if idx in last_rows:
            for key, sigma in additions.get(last_rows[idx], []):
                expected.append(("new", key, sigma))
    return expected, {"replaced": replaced, "counts": counts, "absent": absent}


def bits(a):
    return np.ascontiguousarray(a, dtype="<f8").view("<u8")


def check_splice(lib0, lib1, idx0, idx1, table, table_path, requested_mev, bounds_asc):
    """Compare a spliced build with the unspliced one."""
    out = {}
    rows0, sig0, b0 = lib0["rows"], lib0["sig"], lib0["bounds"]
    rows1, sig1, b1 = lib1["rows"], lib1["sig"], lib1["bounds"]
    ngroups = sig0.shape[1]
    requested_ev = requested_mev * 1.0e6
    first = effective_index(b0.tolist(), requested_ev)
    effective_ev = float(b0[first])
    out["requested_splice_eV"] = requested_ev
    out["effective_splice_eV"] = effective_ev
    out["first_spliced_group"] = first
    out["bounds_identical"] = bool(b0.tobytes() == b1.tobytes())
    expected, plan = simulate(rows0, idx0["targets"], table)
    n_replaced = sum(c["rows_replaced"] for c in plan["counts"])
    n_added = sum(c["rows_added"] for c in plan["counts"])
    out["expected"] = {"rows_replaced": n_replaced, "rows_added": n_added, "per_target": plan["counts"],
                       "targets_not_in_library": plan["absent"]}
    out["precondition_replaces_and_adds"] = bool(n_replaced >= 1 and n_added >= 1 and plan["absent"])
    exp_rows = np.array([tuple(int(v) for v in rows0[e[1]]) if e[0] == "old" else e[1] for e in expected],
                        dtype=np.int64).reshape(-1, 5)
    out["row_count"] = {"unspliced": int(rows0.shape[0]), "spliced": int(rows1.shape[0]),
                        "expected": int(exp_rows.shape[0])}
    out["row_keys_and_order_as_expected"] = bool(rows1.shape == exp_rows.shape and np.array_equal(rows1, exp_rows))
    bad = {"below_splice_changed": [], "unnamed_row_changed": [], "replaced_wrong": [], "added_below_nonzero": [],
           "added_wrong": []}
    if out["row_keys_and_order_as_expected"]:
        s0b, s1b = bits(sig0), bits(sig1)
        for pos, e in enumerate(expected):
            if e[0] == "old":
                idx = e[1]
                if not np.array_equal(s1b[pos, :first], s0b[idx, :first]):
                    bad["below_splice_changed"].append(pos)
                if idx in plan["replaced"]:
                    want = bits(np.array(plan["replaced"][idx]))
                    if not np.array_equal(s1b[pos, first:], want):
                        bad["replaced_wrong"].append(pos)
                elif not np.array_equal(s1b[pos, first:], s0b[idx, first:]):
                    bad["unnamed_row_changed"].append(pos)
            else:
                if not np.array_equal(s1b[pos, :first], np.zeros(first, dtype="<u8")):
                    bad["added_below_nonzero"].append(pos)
                if not np.array_equal(s1b[pos, first:], bits(np.array(e[2]))):
                    bad["added_wrong"].append(pos)
    out["mismatches"] = {k: v[:10] for k, v in bad.items()}
    out["mismatch_counts"] = {k: len(v) for k, v in bad.items()}
    out["old_rows_below_splice_byte_identical"] = out["row_keys_and_order_as_expected"] and not bad["below_splice_changed"]

    # index
    block = idx1.get("spallation")
    idx_out = {"has_block": block is not None}
    table_sha = sha256_file(table_path)
    if block is not None:
        idx_out["table_sha256_matches"] = block.get("table_sha256") == table_sha
        idx_out["schema"] = block.get("schema")
        idx_out["requested_splice_eV"] = block.get("requested_splice_eV")
        idx_out["effective_splice_eV"] = block.get("effective_splice_eV")
        idx_out["requested_ok"] = block.get("requested_splice_eV") == requested_ev
        idx_out["effective_ok"] = block.get("effective_splice_eV") == effective_ev
        idx_out["first_spliced_group_ok"] = block.get("first_spliced_group") == first
        idx_out["groups_spliced_ok"] = block.get("groups_spliced") == ngroups - first
        idx_out["rows_ok"] = (block.get("rows_replaced") == n_replaced and block.get("rows_added") == n_added)
        idx_out["targets_ok"] = block.get("targets") == plan["counts"]
        idx_out["absent_ok"] = block.get("targets_not_in_library") == plan["absent"]
        idx_out["description_ok"] = block.get("table_description") == table["description"]
        idx_out["projectile_ok"] = block.get("table_projectile") == table["projectile"]
    ledgers_ok = True
    n_rows_ok = idx1.get("n_rows") == int(rows1.shape[0]) and idx1.get("sha256_npz") == sha256_file(
        Path(lib1["_path"]))
    t0s, t1s = idx0["targets"], idx1["targets"]
    counts = {(c["za"], c["liso"]): c for c in plan["counts"]}
    target_problems = []
    if len(t0s) != len(t1s):
        target_problems.append("target count changed")
    else:
        for a, b in zip(t0s, t1s):
            c = counts.get((a["za"], a["liso"]))
            if c is None:
                if canonical(a) != canonical(b):
                    target_problems.append(f"untouched target {a['za']} changed")
                continue
            rest_a = {k: v for k, v in a.items() if k not in ("n_rows", "ledger")}
            rest_b = {k: v for k, v in b.items() if k not in ("n_rows", "ledger")}
            if canonical(rest_a) != canonical(rest_b):
                target_problems.append(f"target {a['za']} fields other than n_rows/ledger changed")
            if b["n_rows"] != a["n_rows"] + c["rows_added"]:
                target_problems.append(f"target {a['za']} n_rows")
            added_lines = b["ledger"][len(a["ledger"]):]
            line_ok = (b["ledger"][:len(a["ledger"])] == a["ledger"] and len(added_lines) == 1
                       and added_lines[0].startswith("spallation splice at ")
                       and f"{c['rows_replaced']} row(s) replaced, {c['rows_added']} row(s) added from {table_sha}"
                       in added_lines[0])
            if not line_ok:
                ledgers_ok = False
                target_problems.append(f"target {a['za']} ledger line")
    idx_out["targets_consistent"] = not target_problems
    idx_out["target_problems"] = target_problems[:10]
    idx_out["n_rows_and_npz_hash_ok"] = bool(n_rows_ok)
    idx_out["ledger_lines_ok"] = ledgers_ok
    other_keys = ("schema", "format", "projectile", "temperature_K", "groups", "group_boundary_sha256", "weighting",
                  "builder_fingerprint", "emission_model", "decay_state_table_sha256",
                  "decay_fallback_state_table_sha256", "options", "state_catalog", "build_failures", "columns")
    idx_out["other_index_keys_unchanged"] = all(canonical(idx0.get(k)) == canonical(idx1.get(k)) for k in other_keys)
    idx_out["pass"] = bool(
        block is not None and all(idx_out.get(k) is True for k in (
            "table_sha256_matches", "requested_ok", "effective_ok", "first_spliced_group_ok", "groups_spliced_ok",
            "rows_ok", "targets_ok", "absent_ok", "description_ok", "projectile_ok", "targets_consistent",
            "n_rows_and_npz_hash_ok", "ledger_lines_ok", "other_index_keys_unchanged")))
    out["index"] = idx_out
    out["pass"] = bool(
        out["precondition_replaces_and_adds"] and out["bounds_identical"] and out["row_keys_and_order_as_expected"]
        and not any(bad.values()) and idx_out["pass"])
    return out


def gate_g4(runner, cand, input_dir, base_build, work, decay):
    out = {}
    base_npz = Path(base_build["npz"])
    lib0 = load_npz(base_npz)
    idx0 = json.loads(index_of(base_npz).read_text())
    bounds = [float(b) for b in lib0["bounds"]]
    out["splices"] = {}
    keep_table = None
    ok = True
    for tag, request_mev in (("200MeV", 200.0), ("205MeV", 205.0)):
        entry = {"requested_MeV": request_mev}
        out["splices"][tag] = entry
        try:
            first = effective_index(bounds, request_mev * 1.0e6)
            table = build_table(bounds, first, lib0["rows"], idx0["targets"])
            table_path = work / f"synthetic_table_{tag}.json"
            table_path.write_text(json.dumps(table, indent=1) + "\n")
            entry["table"] = {"path": str(table_path), "sha256": sha256_file(table_path), "groups":
                              len(table["boundaries_eV"]) - 1, "first_boundary_eV": table["boundaries_eV"][0],
                              "targets": [(t["za"], t["liso"]) for t in table["targets"]]}
            out_npz = work / f"spliced_{tag}.npz"
            build = build_library(runner, cand, f"g4_spliced_{tag}", input_dir, out_npz, "fispact-709", decay,
                                  ["--spallation", table_path, "--splice-energy-MeV", f"{request_mev:g}"])
            entry["build"] = {k: build.get(k) for k in ("rc", "seconds", "npz_sha256", "log")}
            if build["rc"] != 0:
                raise RuntimeError("spliced build failed; see log")
            lib1 = load_npz(out_npz)
            lib1["_path"] = str(out_npz)
            idx1 = json.loads(index_of(out_npz).read_text())
            entry["check"] = check_splice(lib0, lib1, idx0, idx1, table, table_path, request_mev, bounds)
            entry["pass"] = entry["check"]["pass"]
            if tag == "200MeV":
                keep_table = (table, table_path)
        except (RuntimeError, ValueError, KeyError, OSError, StopIteration) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        ok = ok and entry["pass"]

    # A table whose boundaries start at 200 MeV must be refused when the splice snaps to 240 MeV.
    refuse = {}
    out["misaligned_table_refused"] = refuse
    try:
        if keep_table is None:
            raise RuntimeError("the 200 MeV table was not generated")
        table, table_path = keep_table
        out_npz = work / "spliced_refused.npz"
        build = build_library(runner, cand, "g4_misaligned", input_dir, out_npz, "fispact-709", decay,
                              ["--spallation", table_path, "--splice-energy-MeV", "205"])
        refuse.update({"rc": build["rc"], "log": build["log"], "npz_written": out_npz.exists(),
                       "index_written": index_of(out_npz).exists()})
        refuse["pass"] = bool(build["rc"] != 0 and not out_npz.exists() and not index_of(out_npz).exists())
    except (RuntimeError, OSError) as error:
        refuse["error"] = f"{type(error).__name__}: {error}"
        refuse["pass"] = False
    ok = ok and refuse["pass"]
    out["pass"] = bool(ok)
    return out


# ------------------------------------------------------------------------------------------------- G5


def lethargy_fluxes(bounds_asc, e_lo, e_hi, total):
    """Group-integrated flux of a spectrum that is constant per unit lethargy between e_lo and e_hi."""
    c = total / math.log(e_hi / e_lo)
    flux = []
    for lo, hi in zip(bounds_asc, bounds_asc[1:]):
        a, b = max(lo, e_lo), min(hi, e_hi)
        flux.append(c * math.log(b / a) if b > a else 0.0)
    return flux


def write_g5_spec(path, structure, flux, npz, element):
    spec = {
        "spec": "actinv-spec-1",
        "title": f"P130 G5 equal-lethargy spectrum on {structure}",
        "projectile": "neutron",
        "library": {"path": str(npz)},
        "decay": {"primary": str(DECAY_PRIMARY), "fallback": str(DECAY_FALLBACK)},
        "material": {"mass_g": 1.0, "basis": "wt_percent", "composition": {element.upper(): 100.0}},
        "spectrum": {"structure": structure, "flux_per_group": flux, "descending": False},
        "schedule": [{"dt": G5_IRRADIATION, "flux": 1.0}],
        "options": {"mode": "auto", "prune": "rate", "bmin_atoms_per_g": 1e-08, "temperature_K": 293.6},
    }
    path.write_text(json.dumps(spec, indent=1) + "\n")
    return spec


def one_group_rates(lib, flux):
    """Loss-row (target, MT) reaction rates per atom: sum_g sigma_g * phi_g, in barn * flux units."""
    phi = np.array(flux, dtype=np.float64)
    rates = {}
    for r, s in zip(lib["rows"], lib["sig"]):
        if int(r[2]) == -1 and int(r[3]) == -1 and int(r[4]) == 0:
            rates[(int(r[0]), int(r[1]))] = float(np.dot(s, phi))
    return rates


def gate_g5(runner, cand, input_dir, base_709, work, decay, element):
    out = {"report_only": True, "tolerance": {"total_activity": G5_TOLERANCE_ACTIVITY, "reaction_rate":
                                              G5_TOLERANCE_RATE, "significant_rate_fraction":
                                              G5_SIGNIFICANT_RATE_FRACTION, "justification": G5_TOLERANCE_TEXT},
           "spectrum": {"shape": "equal flux per unit lethargy", "E_lo_eV": G5_E_LO_EV, "E_hi_eV": G5_E_HI_EV,
                        "total_flux_n_cm2_s": G5_TOTAL_FLUX, "irradiation": G5_IRRADIATION}}
    libs = {"fispact-709": Path(base_709["npz"])}
    build = build_library(runner, cand, "g5_build_1102", input_dir, work / "lib_1102.npz", "fispact-1102", decay)
    out["build_1102"] = {k: build.get(k) for k in ("rc", "seconds", "npz_sha256", "log")}
    if build["rc"] != 0:
        out["error"] = "fispact-1102 library build failed"
        out["within_tolerance"] = None
        return out
    libs["fispact-1102"] = work / "lib_1102.npz"
    per = {}
    out["structures"] = per
    for structure, npz in libs.items():
        entry = per[structure] = {}
        lib = load_npz(npz)
        bounds = [float(b) for b in lib["bounds"]]
        published = json.loads((DATA_DIR / f"fispact_{structure.split('-')[1]}_groups.json").read_text())
        entry["library_bounds_equal_vendored"] = bounds == sorted(published["boundaries_eV"])
        if G5_E_LO_EV not in bounds or G5_E_HI_EV not in bounds:
            raise ValueError(f"{structure} lacks a spectrum edge as a boundary")
        flux = lethargy_fluxes(bounds, G5_E_LO_EV, G5_E_HI_EV, G5_TOTAL_FLUX)
        entry["groups"] = len(flux)
        entry["flux_sum"] = math.fsum(flux)
        entry["flux_sum_relative_error"] = abs(entry["flux_sum"] - G5_TOTAL_FLUX) / G5_TOTAL_FLUX
        spec_path = work / f"g5_{structure}.json"
        write_g5_spec(spec_path, structure, flux, npz, element)
        entry["spec"] = str(spec_path)
        val = runner.run([cand, "validate", spec_path, "--files"], f"g5_validate_{structure}", RUN_TIMEOUT_S, cwd=ROOT)
        entry["validate_rc"] = val["rc"]
        result_path = work / f"g5_{structure}_result.json"
        if result_path.exists():
            result_path.unlink()
        run = runner.run([cand, "run", spec_path, result_path], f"g5_run_{structure}", RUN_TIMEOUT_S, cwd=ROOT)
        entry["run_rc"] = run["rc"]
        entry["run_seconds"] = run["seconds"]
        if run["rc"] != 0 or not result_path.is_file():
            entry["error"] = "run failed; see log"
            continue
        result = json.loads(result_path.read_text())
        step = result["steps"][-1]
        activity = step["activity_Bq_per_g"]
        entry["total_activity_Bq_per_g"] = math.fsum(activity.values())
        entry["activity_by_nuclide"] = activity
        entry["rates"] = one_group_rates(lib, flux)
    a, b = per.get("fispact-709", {}), per.get("fispact-1102", {})
    if "total_activity_Bq_per_g" not in a or "total_activity_Bq_per_g" not in b or "rates" not in a:
        out["error"] = "a G5 run failed"
        out["within_tolerance"] = None
        for entry in per.values():
            entry.pop("rates", None)
            entry.pop("activity_by_nuclide", None)
        return out
    ratio = b["total_activity_Bq_per_g"] / a["total_activity_Bq_per_g"] if a["total_activity_Bq_per_g"] else None
    out["total_activity"] = {"fispact_709_Bq_per_g": a["total_activity_Bq_per_g"],
                             "fispact_1102_Bq_per_g": b["total_activity_Bq_per_g"], "ratio_1102_over_709": ratio}
    act_709, act_1102 = a.pop("activity_by_nuclide"), b.pop("activity_by_nuclide")
    big = [n for n, v in act_709.items() if v >= 1e-3 * a["total_activity_Bq_per_g"]]
    out["dominant_nuclide_activity_ratios"] = {
        n: (act_1102.get(n, 0.0) / act_709[n]) for n in sorted(big)}
    r709, r1102 = a.pop("rates"), b.pop("rates")
    top = max(r709.values()) if r709 else 0.0
    rate_ratios = {}
    for key in sorted(set(r709) & set(r1102)):
        if r709[key] > 0.0:
            rate_ratios[key] = r1102[key] / r709[key]
    significant = {k: v for k, v in rate_ratios.items() if r709[k] >= G5_SIGNIFICANT_RATE_FRACTION * top}
    worst = max(significant.items(), key=lambda kv: abs(kv[1] - 1.0)) if significant else None
    out["reaction_rates"] = {
        "loss_rows_compared": len(rate_ratios), "rows_only_in_one": len(set(r709) ^ set(r1102)),
        "significant_rows": len(significant),
        "worst_significant_ratio": None if worst is None else {"target": worst[0][0], "mt": worst[0][1],
                                                                "ratio_1102_over_709": worst[1]},
        "significant_ratios": {f"t{k[0]}_mt{k[1]}": v for k, v in significant.items()}}
    act_ok = ratio is not None and abs(ratio - 1.0) <= G5_TOLERANCE_ACTIVITY
    rate_ok = worst is not None and abs(worst[1] - 1.0) <= G5_TOLERANCE_RATE
    out["within_tolerance"] = bool(act_ok and rate_ok)
    out["activity_within_tolerance"] = bool(act_ok)
    out["rate_within_tolerance"] = bool(rate_ok)
    out["both_validate"] = all(e.get("validate_rc") == 0 for e in per.values())
    return out


# ------------------------------------------------------------------------------------------------- driver


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True, help="actinv binary built from the base commit")
    ap.add_argument("--candidate", required=True, help="actinv binary built from the P130 commit")
    ap.add_argument("--workdir", default="target/p130-work")
    ap.add_argument("--out", default="results/p130_groups.json")
    ap.add_argument("--archive", default=str(ARCHIVE), help="FISPACT-II ebins.tar.bz2")
    ap.add_argument("--tendl-dir", default=str(TENDL_DIR), help="TENDL-2025 neutron evaluations (n-XxNNN.tendl)")
    ap.add_argument("--isotopes", default="Mg024,Mg025,Mg026",
                    help="comma-separated isotope file stems after 'n-' for the small one-element library")
    ap.add_argument("--no-decay", action="store_true", help="build libraries without --decay (runs still need it)")
    args = ap.parse_args(argv)

    work = Path(args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    ref, cand = Path(args.reference).resolve(), Path(args.candidate).resolve()
    isotopes = [s for s in args.isotopes.split(",") if s]
    element = re.match(r"[A-Za-z]+", isotopes[0]).group(0)
    decay = not args.no_decay
    record = {"protocol": "ACTINV-P130", "protocol_sha256": sha256_file(PROTOCOL),
              "reference": str(ref), "reference_sha256": sha256_file(ref),
              "candidate": str(cand), "candidate_sha256": sha256_file(cand),
              "library_inputs": {"tendl_dir": args.tendl_dir, "isotopes": isotopes, "element": element,
                                 "decay_in_builds": decay},
              "gates": {}}
    runner = Runner(work)
    gates = record["gates"]

    def guarded(name, fn):
        try:
            gates[name] = fn()
        except Exception as error:  # a gate never crashes the run; its error is the evidence
            gates[name] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        print(f"{name}: {'PASS' if gates[name].get('pass') else 'FAIL'}", flush=True)

    if np is None:
        for name in ("G2", "G3", "G4", "G5"):
            gates[name] = {"pass": False, "error": "numpy is required"}
    else:
        guarded("G2", lambda: gate_g2(Path(args.archive)))

        input_dir = work / "inputs" / element.lower()
        built = {}

        def g3():
            inputs = stage_inputs(Path(args.tendl_dir), isotopes, input_dir)
            result = {"input_files_sha256": inputs}
            result["fns_fe_5min"] = gate_g3_fns(runner, ref, cand, work)
            builds = {}
            for label, binary in (("reference", ref), ("candidate", cand)):
                builds[label] = build_library(runner, binary, f"g3_build_{label}", input_dir,
                                              work / f"lib_709_{label}.npz", "fispact-709", decay)
            built.update(builds)
            result["library"] = gate_g3_library(builds["reference"], builds["candidate"])
            result["pass"] = bool(result["fns_fe_5min"]["pass"] and result["library"]["pass"])
            return result

        guarded("G3", g3)
        usable = "candidate" in built and built["candidate"]["rc"] == 0

        def need_library(fn):
            if not usable:
                raise RuntimeError("the candidate 709-group library from G3 is unavailable")
            return fn()

        guarded("G4", lambda: need_library(lambda: gate_g4(runner, cand, input_dir, built["candidate"], work,
                                                           decay)))
        guarded("G5", lambda: need_library(lambda: gate_g5(runner, cand, input_dir, built["candidate"], work,
                                                           decay, element)))
    verdict = all(gates[g].get("pass") is True for g in ("G2", "G3", "G4"))
    record["verdict"] = "PASS" if verdict else "FAIL"
    record["pass"] = verdict
    record["g5_within_tolerance_report_only"] = gates["G5"].get("within_tolerance")
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
    print(f"P130: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
