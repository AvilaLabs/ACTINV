#!/usr/bin/env python3
"""P25c G2: surgical patcher for the confirmed TENDL-2025 leak.

Frozen patch specification (from the P25c protocol and G1 census):

  For every signature record enumerated in ``results/g1_p25c_signature.json``
  — (file, MF, MT, ZAP, LFS, contaminated leading ordinate count) — locate
  every physical MF=9/10 sub-record in that file whose head line matches
  (ZAP, LFS), and set the enumerated leading cross-section ordinates to
  zero, preserving the 11-column ENDF field and the record's line length.
  No other byte of any file changes.  Files outside the sealed signature
  set are copied verbatim.

The derived corpus is written under
``~/nuclear-data/tendl-2025-patched/files/n/`` with a per-file
old→new SHA-256 manifest.  The patch log records every edited
(file, line, column, field) so an independent checker can replay the
entire corpus derivation from source bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))
from p18b_decimal_corpus_oracle import fields, integer, parse_exact  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
SIGNATURE = RESULTS / "g1_p25c_signature.json"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
PATCHED_MANIFEST = RESULTS / "g2_p25c_patched_manifest.sha256"

ZERO_FIELD = " 0.000000+0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def section_lines(lines: list[str], mf: int, mt: int, mat: int) -> tuple[int, int]:
    """Return (start, end) line indices of the (MF,MT) section for mat."""
    start = None
    for i, ln in enumerate(lines):
        if len(ln) < 75:
            continue
        try:
            m_, f_, t_ = int(ln[66:70]), int(ln[70:72]), int(ln[72:75])
        except ValueError:
            continue
        if m_ != mat or f_ <= 0 or t_ <= 0:
            continue
        if f_ == mf and t_ == mt:
            if start is None:
                start = i
            end = i
    if start is None:
        raise ValueError(f"no MF={mf}/MT={mt} section")
    return start, end + 1


def walk_subrecords(lines: list[str], lo: int, hi: int):
    """Yield (head_line, data_start_line, np) per MF=9/10 sub-record."""
    count = integer(fields(lines[lo])[4])
    idx = lo + 1
    for _ in range(count):
        head = fields(lines[idx])
        nr, np_ = integer(head[4]), integer(head[5])
        # skip interpolation lines: NR ranges, up to 3 pairs per line
        interp_lines = (nr + 2) // 3
        data_start = idx + 1 + interp_lines
        data_lines = (np_ + 2) // 3
        yield idx, data_start, np_
        idx = data_start + data_lines


def patch_file(name: str, sig_records: list[dict]) -> tuple[bytes, list[dict]]:
    """Apply the frozen patch to one file; return (new bytes, patch log)."""
    raw = (SOURCE_ROOT / name).read_bytes()
    text = raw.decode("ascii")
    lines = text.splitlines(keepends=True)
    mat = None
    for ln in lines[:12]:
        try:
            if int(ln[70:72]) == 1 and int(ln[72:75]) == 451:
                mat = int(ln[66:70])
                break
        except (ValueError, IndexError):
            pass
    if mat is None:
        raise ValueError(f"{name}: no MF=1/MT=451 header")

    log: list[dict] = []
    for sig in sig_records:
        mf, mt = sig["mf"], sig["mt"]
        lo, hi = section_lines([l.rstrip("\r\n") for l in lines], mf, mt, mat)
        n_zero = sig["contaminated_leading_ordinates"]
        for head_i, data_i, np_ in walk_subrecords(
                [l.rstrip("\r\n") for l in lines], lo, hi):
            head = fields(lines[head_i].rstrip("\r\n"))
            if int(head[2]) != sig["zap"] or int(head[3]) != sig["lfs"]:
                continue
            # zero leading ordinates: ordinate j sits at field column
            # 11+22*(j%3) within data line data_i + j//3
            ref = Decimal(sig["leaked_value_barn"])
            for j in range(n_zero):
                li = data_i + j // 3
                col = 11 + 22 * (j % 3)
                orig = lines[li]
                stripped = orig.rstrip("\r\n")
                old_field = stripped[col:col + 11]
                if parse_exact(old_field).value != ref:
                    raise ValueError(
                        f"{name} MF{mf}/MT{mt} ordinate {j}: field "
                        f"{old_field!r} != enumerated leak {ref}")
                eol = orig[len(stripped):]
                new_line = stripped[:col] + ZERO_FIELD + stripped[col + 11:] + eol
                if len(new_line.rstrip("\r\n")) != len(stripped):
                    raise ValueError(f"{name}: line {li+1} length changed")
                lines[li] = new_line
                log.append({
                    "file": name, "mf": mf, "mt": mt,
                    "zap": sig["zap"], "lfs": sig["lfs"],
                    "line": li + 1, "column": col + 1,
                    "ordinate_index": j,
                    "old_field": old_field, "new_field": ZERO_FIELD.strip(),
                })
    return "".join(lines).encode("ascii"), log


def main() -> int:
    t0 = time.time()
    seals = json.loads(SEALS.read_text())
    sig_census = json.loads(SIGNATURE.read_text())

    patch_population = {
        name: rec["signature_records"]
        for name, rec in sig_census["files"].items()
        if rec["class"] == "confirmed_leak_signature"
    }
    PATCHED_ROOT.mkdir(parents=True, exist_ok=True)

    manifest = {}
    patch_log: list[dict] = []
    copied = patched = 0
    for src in sorted(SOURCE_ROOT.glob("*.tendl")):
        name = src.name
        old_sha = sha256(src)
        if name in patch_population:
            data, log = patch_file(name, patch_population[name])
            patch_log.extend(log)
            patched += 1
        else:
            data = src.read_bytes()
            copied += 1
        (PATCHED_ROOT / name).write_bytes(data)
        new_sha = sha256_bytes(data)
        manifest[name] = {"old_sha256": old_sha, "new_sha256": new_sha,
                          "patched": name in patch_population}
        if name in patch_population and new_sha == old_sha:
            raise AssertionError(f"{name}: patch produced identical bytes")

    PATCHED_MANIFEST.write_text(
        "".join(f"{v['new_sha256']}  {k}\n" for k, v in sorted(manifest.items()))
    )
    record = {
        "schema": "actinv-p25c-g2-patch-1",
        "gate": "P25c-G2",
        "seals_sha256": sha256(SEALS),
        "signature_sha256": sha256(SIGNATURE),
        "source_root": str(SOURCE_ROOT),
        "patched_root": str(PATCHED_ROOT),
        "file_count": len(manifest),
        "patched_files": patched,
        "copied_verbatim": copied,
        "edit_count": len(patch_log),
        "patch_log_sha256": hashlib.sha256(
            json.dumps(patch_log, sort_keys=True).encode()).hexdigest(),
        "patch_log": patch_log,
        "manifest_file": str(PATCHED_MANIFEST),
        "manifest_sha256": sha256(PATCHED_MANIFEST),
        "elapsed_s": round(time.time() - t0, 3),
    }
    out = RESULTS / "g2_p25c_patch.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"G2 patch: {patched} patched, {copied} copied verbatim, "
          f"{len(patch_log)} ordinate edits, {record['elapsed_s']}s")
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
