#!/usr/bin/env python3
"""P24 G1 independent checker.

Re-derives the corrected measurement-definition demonstration and the
evaluated-state alias catalog without importing ``p24_definitions``
(the module under test) or the P17 held-out scorer.  Verifies:

  - the frozen definition-module file hash matches the record;
  - source-document hashes verify;
  - the D1 pulse-limit reconstruction reproduces, from the printed
    P17 evidence rows, the publication's inferred spectral indices —
    recomputed here with independent arithmetic;
  - the Ag109g -> LFS=2 binding and the multi-declared -> MF=3 total
    convention are independently re-derived from the hash-pinned
    IRDFF-II file;
  - the results allowlist still contains no fresh-table names;
  - planted mutations of the record are rejected.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g1_p24_definitions.json"
DEF_MODULE = REPO / "controls" / "p24_definitions.py"
P17_HELDOUT = RESULTS / "g5_p17_heldout.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
POINTWISE = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_ENDF.zip"
DECAY = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_dd_ENDF.zip"
PAPER = Path.home() / "nuclear-data/p17-irdff/IRDFF-II_primary_1909.03336.pdf"
FRESH_TABLES = set(range(26, 48)) - {32, 33, 47}
FRESH_TABLES |= {32, 33}  # full fresh set incl. separate-located tables

failures: list[str] = []


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ef(s: str) -> float:
    """Independent ENDF float parse incl. space-padded exponents."""
    s = str(s).strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return float(re.sub(r"([+-])\s*(\d+)$", r"e\1\2", s))


def fields(line: str) -> list[str]:
    return [line[i * 11:(i + 1) * 11] for i in range(6)]


def sections(path: Path):
    cur, buf = None, []
    with open(path, errors="replace") as fh:
        for line in fh:
            if len(line) < 75:
                continue
            try:
                mat, mf, mt = int(line[66:70]), int(line[70:72]), int(line[72:75])
            except ValueError:
                continue
            if mf == 0 or mt == 0 or mat <= 0:
                if cur is not None and buf:
                    yield cur, buf
                cur, buf = None, []
                continue
            if (mat, mf, mt) != cur:
                if cur is not None and buf:
                    yield cur, buf
                cur, buf = (mat, mf, mt), []
            buf.append(line)
    if cur is not None and buf:
        yield cur, buf


def tab1_head_skip(lines: list[str], i: int) -> tuple[tuple, int]:
    f = fields(lines[i])
    head = (ef(f[0]), ef(f[1]), int(f[2]), int(f[3]), int(f[4]), int(f[5]))
    i += 1
    cnt = 0
    while cnt < head[4]:
        i += 1
        cnt += 3
    cnt = 0
    while cnt < head[5]:
        i += 1
        cnt += 3
    return head, i


def independent_declared_catalog(member: Path) -> dict[tuple[int, int], list[tuple[int, int]]]:
    decl: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for (_, mf, mt), lines in sections(member):
        if mf != 10:
            continue
        za = int(ef(fields(lines[0])[0]))
        nsub = int(ef(fields(lines[0])[4]))
        off = 1
        for _ in range(nsub):
            head, off = tab1_head_skip(lines, off)
            decl[(za, mt)].append((head[3], int(head[2])))
    return {k: sorted(v) for k, v in decl.items()}


def independent_decay_identities(member: Path) -> dict[tuple[int, int], float]:
    """(za,liso) -> half-life of the largest-MAT record.  Own parser."""
    out: dict[tuple[int, int], tuple[int, float]] = {}
    for (mat, mf, mt), lines in sections(member):
        if mf != 8 or mt != 457:
            continue
        f = fields(lines[0])
        za, liso = int(ef(f[0])), int(f[3])
        # T1/2 is the first value of the first LIST after the head card
        g = fields(lines[1])
        t12 = ef(g[0])
        key = (za, liso)
        if key not in out or mat > out[key][0]:
            out[key] = (mat, t12)
    return {k: v[1] for k, v in out.items()}


def check_report(record: dict, live_module_hash: str | None = None) -> dict:
    local: list[str] = []
    if live_module_hash is not None:
        if record.get("frozen_definition_module", {}).get("sha256") != live_module_hash:
            local.append("frozen definition module hash mismatch")
    a = record.get("authority", {})
    if not a.get("protocol_sha256_verified"):
        local.append("authority.protocol_sha256_verified is not true")
    if not a.get("g0_pass"):
        local.append("authority.g0_pass is not true")
    demo = record.get("d1_demonstration_on_consumed_partition", {})
    per_row = demo.get("per_row", [])
    if not per_row:
        local.append("demonstration has no per-row records")
    out_of_unc = [r["row_id"] for r in per_row
                  if r.get("within_printed_experimental_uncertainty") is False]
    # every row that is out of uncertainty bound must be individually
    # identified in the record (no silent failures)
    if out_of_unc and demo.get("worst_residual", {}).get("row_id") not in out_of_unc:
        local.append("worst out-of-uncertainty row not identified")
    if not record.get("alias_table"):
        local.append("alias_table missing or empty")
    if record.get("fresh_partition_values_read") is not False:
        local.append("fresh_partition_values_read is not false")
    return {"pass": not local, "failures": local}


def main() -> int:
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    record = json.loads(RECORD.read_text())

    if sha256(DEF_MODULE) != record["frozen_definition_module"]["sha256"]:
        failures.append("frozen definition module hash mismatch")

    for key, path in (("pointwise_archive_sha256", POINTWISE),
                      ("decay_archive_sha256", DECAY),
                      ("primary_pdf_sha256", PAPER)):
        if path.exists():
            if sha256(path) != record["source_documents"][key]:
                failures.append(f"{key} mismatch")

    # --- independent D1 re-derivation -------------------------------------
    heldout = json.loads(P17_HELDOUT.read_text())
    rows = [r for r in heldout["rows"] if r["family"] in
            {"H1_SPR_III_table_23", "H2_ACRR_table_25"}]
    rec_rows = {r["row_id"]: r for r in record["d1_demonstration_on_consumed_partition"]["per_row"]}
    heldout_ids = {r["row_id"] for r in rows}
    if heldout_ids != set(rec_rows):
        failures.append("demonstration row set does not match the consumed partition")

    with tempfile.TemporaryDirectory(dir=REPO / "target/preflight-tmp") as td:
        with zipfile.ZipFile(DECAY) as zf:
            zf.extract("IRDFF-II_dd.endf", td)
        decays = independent_decay_identities(Path(td) / "IRDFF-II_dd.endf")
        with zipfile.ZipFile(POINTWISE) as zf:
            zf.extract("IRDFF-II.endf", td)
        decl = independent_declared_catalog(Path(td) / "IRDFF-II.endf")

    lam_m = math.log(2.0) / decays[(27058, 0)]
    monitor_eoi = {}
    for r in rows:
        if r["source_record"]["label"].startswith("Ni58p"):
            monitor_eoi[r["family"]] = r["source_record"]["measured_EOI_per_atom"]
    n_ok = 0
    n_total = 0
    worst = (None, 0.0)
    for r in rows:
        s = r["source_record"]
        ev = r["heldout_evidence"]
        if s.get("published_spectral_index") in (None, 0) or s.get("published_SI_C_over_E") in (None, 0):
            continue
        inferred = s["published_spectral_index"] / s["published_SI_C_over_E"]
        am = monitor_eoi.get(r["family"])
        if am is None:
            continue
        ratio = s["measured_EOI_per_atom"] / am
        is_fission = ev["frozen_amendment_1_mapping"].get("is_fission", False)
        if is_fission:
            mine = ratio * lam_m
        else:
            ident = (ev.get("post_failure_pulse_reconstruction") or {}).get("product_decay_identity")
            if not ident:
                continue
            hl = decays.get((int(ident["za"]), int(ident["liso"])))
            if not hl:
                continue
            mine = ratio * lam_m / (math.log(2.0) / hl)
        resid = abs(mine - inferred) / inferred
        n_total += 1
        unc = s.get("experimental_uncertainty_percent")
        if unc is not None and resid <= unc / 100.0:
            n_ok += 1
        if resid > worst[1]:
            worst = (r["row_id"], resid)
        # cross-check against the recorded reconstruction
        rec_r = rec_rows.get(r["row_id"], {})
        rec_v = rec_r.get("corrected_pulse_reconstruction")
        if rec_v is not None and abs(rec_v - mine) / max(abs(mine), 1e-30) > 1e-9:
            failures.append(f"{r['row_id']}: recorded reconstruction {rec_v} != independent {mine}")

    demo = record["d1_demonstration_on_consumed_partition"]
    if demo["rows_reconstructed"] != n_total:
        failures.append(f"reconstructed count {demo['rows_reconstructed']} != {n_total}")
    if demo["rows_within_printed_experimental_uncertainty"] != n_ok:
        failures.append("within-uncertainty count mismatch")
    if worst[0] is not None and demo["worst_residual"]["row_id"] != worst[0]:
        failures.append("worst residual row mismatch")

    # --- independent D2 re-derivation -------------------------------------
    alias = {(e["target_za"], e["mt"], e["interpreted_suffix"]): e["binding"]
             for e in record["alias_table"]}
    # every declared (za,mt) group must appear in the alias table
    declared_keys = set(decl)
    for k in declared_keys:
        for role in ("bare", "explicit_ground", "isomer"):
            if (k[0], k[1], role) not in alias:
                failures.append(f"alias table missing {k} {role}")
    # flagship bindings re-derived from the raw file
    ag = decl.get((47109, 102))
    if ag != [(2, 47110)]:
        failures.append(f"Ag-109 MT102 declared states {ag} != [(2,47110)]")
    b = alias.get((47109, 102, "bare"), {})
    if not (b.get("status") == "bound" and b.get("raw_evaluation_lfs") == 2):
        failures.append("Ag109g bare binding does not land on LFS=2")
    nb = alias.get((41093, 102, "bare"), {})
    if nb.get("channel") != "mf3_total":
        failures.append("Nb93g bare binding is not the MF=3 total channel")
    ingg = alias.get((49113, 102, "explicit_ground"), {})
    if ingg.get("raw_evaluation_lfs") != 0:
        failures.append("In113gg does not bind declared LFS=0")
    ingm = alias.get((49113, 102, "isomer"), {})
    if ingm.get("raw_evaluation_lfs") != 1:
        failures.append("In113gm does not bind declared LFS=1")

    # --- citation spot-checks on the pinned PDF ---------------------------
    if PAPER.exists():
        checks = [
            (73, "16-minute"), (73, "13127"),
            (75, "rigorously"), (77, "153.2"), (77, "10639"),
            (74, "cover"),
        ]
        for page, needle in checks:
            out = subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PAPER), "-"],
                capture_output=True, text=True).stdout
            if needle.lower() not in out.lower():
                failures.append(f"citation check failed: '{needle}' not on PDF page {page}")

    # --- results allowlist: no fresh-table names --------------------------
    names = " ".join(p.name for p in RESULTS.glob("*.json"))
    for t in sorted(FRESH_TABLES):
        if re.search(rf"t(?:able)?[-_]?{t}\b", names):
            failures.append(f"fresh table {t} appears in results filenames")

    # --- structural report check + mutations ------------------------------
    live_hash = sha256(DEF_MODULE)
    rep = check_report(record, live_hash)
    if not rep["pass"]:
        failures.extend(rep["failures"])

    mutations = 0
    rejected = 0
    plants = [
        lambda r: r["d1_demonstration_on_consumed_partition"].update(
            worst_residual={"row_id": "forged", "residual_relative": 0.0}),
        lambda r: r.update(fresh_partition_values_read=True),
        lambda r: r["authority"].update(g0_pass=False),
        lambda r: r["alias_table"].clear(),
        lambda r: r["frozen_definition_module"].update(sha256="0" * 64),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if not check_report(m, live_hash)["pass"]:
            rejected += 1
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p24-g1-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "independent_reconstruction": {
            "rows_reconstructed": n_total,
            "rows_within_printed_uncertainty": n_ok,
            "worst_row": worst[0],
            "worst_residual": worst[1],
        },
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g1_p24_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
