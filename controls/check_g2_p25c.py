#!/usr/bin/env python3
"""P25c G2 independent checker.

Independently replays the entire derived corpus from source bytes: for
each patched file it re-walks the ENDF sub-records with its own parser,
locates the enumerated (MF, MT, ZAP, LFS) records, verifies every
patched ordinate's original field parses exactly to the census's leaked
value, applies the zero edit itself, and requires byte-identity with the
written corpus.  Every byte-level difference between source and patched
file must be covered by a patch-log entry; every non-signature file must
be byte-identical to the pinned source.  Rejects planted mutations.

Imports the frozen oracle's field parser only.  Exit nonzero on failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))
from p18b_decimal_corpus_oracle import fields, integer, parse_exact  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
SIGNATURE = RESULTS / "g1_p25c_signature.json"
RECORD = RESULTS / "g2_p25c_patch.json"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"

ZERO_FIELD = " 0.000000+0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def replay_patch(name: str, sig_records: list[dict]) -> tuple[bytes, list[dict]]:
    """Independent re-derivation of one patched file."""
    lines = (SOURCE_ROOT / name).read_text("ascii").splitlines(keepends=True)
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
    stripped = [l.rstrip("\r\n") for l in lines]
    log: list[dict] = []

    for sig in sig_records:
        mf, mt = sig["mf"], sig["mt"]
        lo = None
        for i, ln in enumerate(stripped):
            if len(ln) < 75:
                continue
            try:
                m_, f_, t_ = int(ln[66:70]), int(ln[70:72]), int(ln[72:75])
            except ValueError:
                continue
            if m_ == mat and f_ == mf and t_ == mt:
                if lo is None:
                    lo = i
                hi = i
        if lo is None:
            raise ValueError(f"{name}: no MF={mf}/MT={mt} section")
        count = integer(fields(stripped[lo])[4])
        idx = lo + 1
        for _ in range(count):
            head = fields(stripped[idx])
            nr, np_ = integer(head[4]), integer(head[5])
            data_start = idx + 1 + (nr + 2) // 3
            data_lines = (np_ + 2) // 3
            if int(head[2]) == sig["zap"] and int(head[3]) == sig["lfs"]:
                ref = Decimal(sig["leaked_value_barn"])
                for j in range(sig["contaminated_leading_ordinates"]):
                    li = data_start + j // 3
                    col = 11 + 22 * (j % 3)
                    old = stripped[li][col:col + 11]
                    if parse_exact(old).value != ref:
                        raise ValueError(
                            f"{name} MF{mf}/MT{mt} ord {j}: {old!r} != {ref}")
                    eol = lines[li][len(stripped[li]):]
                    lines[li] = (stripped[li][:col] + ZERO_FIELD
                                 + stripped[li][col + 11:] + eol)
                    stripped[li] = lines[li].rstrip("\r\n")
                    log.append({"file": name, "mf": mf, "mt": mt,
                                "zap": sig["zap"], "lfs": sig["lfs"],
                                "line": li + 1, "column": col + 1,
                                "ordinate_index": j, "old_field": old,
                                "new_field": ZERO_FIELD.strip()})
            idx = data_start + data_lines
    return "".join(lines).encode("ascii"), log


def check_report(record: dict) -> list[str]:
    local = []
    if record.get("schema") != "actinv-p25c-g2-patch-1":
        local.append("schema")
    if record.get("seals_sha256") != sha256(SEALS):
        local.append("seals hash")
    if record.get("signature_sha256") != sha256(SIGNATURE):
        local.append("signature hash")
    if record.get("file_count") != 2850:
        local.append("file_count")
    if record.get("patched_files") != 28:
        local.append("patched_files")
    if record.get("copied_verbatim") != 2822:
        local.append("copied_verbatim")
    if record.get("edit_count") != len(record.get("patch_log", ())):
        local.append("edit_count != patch_log length")
    if record.get("edit_count") != 44:
        local.append("edit_count != 44")
    if record.get("patch_log_sha256") != hashlib.sha256(
            json.dumps(record.get("patch_log"), sort_keys=True)
            .encode()).hexdigest():
        local.append("patch_log hash")
    man = Path(record.get("manifest_file", ""))
    if not man.is_file() or sha256_bytes(man.read_bytes()) != record.get(
            "manifest_sha256"):
        local.append("patched manifest hash")
    return local


def main() -> int:
    failures: list[str] = []
    seals = json.loads(SEALS.read_text())
    sig = json.loads(SIGNATURE.read_text())
    record = json.loads(RECORD.read_text())

    patch_pop = {
        n: r["signature_records"] for n, r in sig["files"].items()
        if r["class"] == "confirmed_leak_signature"
    }
    expected_log: dict[tuple, dict] = {}
    for name in sorted(patch_pop):
        replayed, log = replay_patch(name, patch_pop[name])
        disk = (PATCHED_ROOT / name).read_bytes()
        if replayed != disk:
            failures.append(f"{name}: replayed bytes != written corpus")
        # every source↔patched byte difference must be inside a logged cell
        src = (SOURCE_ROOT / name).read_text("ascii").splitlines()
        for e in log:
            expected_log[(name, e["line"], e["column"])] = e
        # coverage: count actual differing lines vs logged lines
        new_lines = disk.decode("ascii").splitlines()
        diff_lines = {i + 1 for i, (a, b) in enumerate(zip(src, new_lines))
                      if a != b}
        log_lines = {e["line"] for e in log}
        if diff_lines != log_lines:
            failures.append(
                f"{name}: diff lines {sorted(diff_lines)} != "
                f"logged lines {sorted(log_lines)}")

    rec_log = {(e["file"], e["line"], e["column"]): e
               for e in record.get("patch_log", ())}
    if rec_log != expected_log:
        failures.append("recorded patch_log != independent replay")

    # non-population files must be byte-identical
    for src in sorted(SOURCE_ROOT.glob("*.tendl")):
        if src.name in patch_pop:
            continue
        if src.read_bytes() != (PATCHED_ROOT / src.name).read_bytes():
            failures.append(f"{src.name}: unpatched file diverged")
            break

    # patched files must still parse under the oracle and now read zero
    for name, recs in sorted(patch_pop.items()):
        plines = (PATCHED_ROOT / name).read_text("ascii").splitlines()
        for srec in recs:
            hits = [e for e in record["patch_log"]
                    if e["file"] == name and e["mf"] == srec["mf"]
                    and e["mt"] == srec["mt"] and e["zap"] == srec["zap"]
                    and e["lfs"] == srec["lfs"]]
            if len(hits) != srec["contaminated_leading_ordinates"]:
                failures.append(
                    f"{name} MF{srec['mf']}/MT{srec['mt']}: "
                    f"{len(hits)} edits != {srec['contaminated_leading_ordinates']}")
            for e in hits:
                fld = plines[e["line"] - 1][e["column"] - 1:e["column"] + 10]
                if parse_exact(fld).value != 0:
                    failures.append(f"{name}: line {e['line']} not zeroed")

    failures.extend(check_report(record))

    mutations = rejected = 0
    plants = [
        lambda r: r["patch_log"].append(
            {"file": "n-Ag109.tendl", "mf": 10, "mt": 107, "zap": 1,
             "lfs": 0, "line": 1, "column": 1, "ordinate_index": 0,
             "old_field": " 1.0+0", "new_field": "0.000000+0"}),
        lambda r: r.update({"edit_count": 45}),
        lambda r: r.update({"patched_files": 27}),
        lambda r: r["patch_log"][0].update({"old_field": " 9.9+9"}),
        lambda r: r.update({"seals_sha256": "0" * 64}),
        lambda r: r.update({"manifest_sha256": "0" * 64}),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_report(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25c-g2-check-1",
        "pass": not failures,
        "failures": failures,
        "replayed_files": len(patch_pop),
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g2_p25c_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
