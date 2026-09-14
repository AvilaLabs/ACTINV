#!/usr/bin/env python3
"""P25b G0 opening seal: candidate corpus pins, format census, hold check.

Hash-pins the three on-disk candidate corpora (directory manifests),
classifies every file by the format markers the builder's
`detected_format` uses (EAF-2010 marker, TENDL- marker, or an MF2/MF6
section), runs one bounded end-to-end builder demonstration per corpus,
rehashes the P24/P25 evidence, re-verifies every prior verdict
verbatim, and confirms the release hold stands (no v1.1* tag, 1.1.0
unreleased in CHANGELOG).

The sniff census is a marker-level classification, not a per-file
builder parse: per-target builds are G1's bounded job.  The
demonstration builds show at least one file per corpus parses and
builds end-to-end through `actinv build-library --format auto`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
PROTOCOL = REPO / "protocols" / "ACTINV-P25b_PROTOCOL.md"
PROTOCOL_SHA256 = "3eec3c26db02e4aae0081818df5d1c2effee52f299e7a36e155d4437b08e71be"
OPENING_COMMIT = "f45c830693357a54e2d6b0db8d9fdfd2b3cb0f47"

ND = Path.home() / "nuclear-data"
CORPORA = {
    "tendl_2023": {
        "root": ND / "tendl-2023" / "files",
        "glob": "*.dat",
        "provenance_file": ND / "tendl-2023" / "files.txt",
        "manifest_file": ND / "tendl-2023" / "MANIFEST_zips.sha256",
        "demo": "n_026-Fe-56_2631.dat",
    },
    "fendl_32c": {
        "root": ND / "fendl-3.2c" / "endf",
        "glob": "*.endf",
        "provenance_file": ND / "fendl-3.2c" / "endf_files.txt",
        "manifest_file": ND / "fendl-3.2c" / "MANIFEST_endf.sha256",
        "demo": "n_2631_26-Fe-56.endf",
    },
    "eaf_2010": {
        "root": ND / "eaf-2010" / "files",
        "glob": "*.dat",
        "provenance_file": None,
        "manifest_file": None,
        "demo": "n_2631_26-FE-56.dat",
    },
}

VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
    "verdict_p26.json": "P26-FAIL",
}

PRIOR_EVIDENCE = [
    "g1_p25_census.json",
    "g2_p25_traces.json",
    "g4_p25_repairs.json",
    "g5_p25_acceptance.json",
    "g1_p18b_decimal_oracle.json",
    "g4_p24_fresh.json",
    "g4_p24_row_ledger.json",
    "p24_cause_ledger.json",
]

PRIOR_DOCS = [
    REPO / "docs" / "P25_TENDL2025_DEFECT_REPORT.md",
    REPO / "docs" / "COMPETITIVE_BENCHMARK.md",
]

NUMERIC = re.compile(rb"^[ 0-9+\-.Ee]+$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff(path: Path) -> str:
    """Marker-level format class matching builder `detected_format` order."""
    try:
        text = path.read_bytes()
    except OSError:
        return "unreadable"
    if b"EAF-2010" in text or b"EAF-20100" in text:
        return "eaf_marked"
    if b"TENDL-" in text:
        return "tendl_marked"
    has_endf_tail = False
    for raw in text.split(b"\n"):
        line = raw.rstrip(b"\r")
        if len(line) < 75:
            continue
        mat, mf, mt = line[66:70].strip(), line[70:72], line[72:75].strip()
        if mat.isdigit() and mt.isdigit() and mf in (b" 1", b" 2", b" 3",
                                                   b" 4", b" 5", b" 6"):
            has_endf_tail = True
            if mf in (b" 2", b" 6"):
                return "endf_mf2_or_mf6"
    if has_endf_tail:
        return "endf_no_detectable_marker"
    return "format_unsupported"


def build_manifest(root: Path, pattern: str) -> tuple[dict, dict]:
    """Return ({relpath: sha256}, census histogram)."""
    entries, census = {}, {}
    for path in sorted(root.rglob(pattern)):
        rel = path.relative_to(root).as_posix()
        entries[rel] = sha256(path)
        cls = sniff(path)
        census[cls] = census.get(cls, 0) + 1
    return entries, census


def demonstration(corpus: str, spec: dict) -> dict:
    src = spec["root"] / spec["demo"]
    out = REPO / "target" / "preflight-tmp" / f"p25b_{corpus}.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["actinv", "build-library", str(src), str(out),
             "--format", "auto"],
            capture_output=True, text=True, timeout=180)
        rec = {
            "file": spec["demo"],
            "command": f"actinv build-library {src} {out} --format auto",
            "exit": proc.returncode,
            "seconds": round(time.monotonic() - t0, 3),
            "npz_sha256": sha256(out) if out.is_file() else None,
            "stderr_tail": proc.stderr.strip()[-400:] if proc.returncode else "",
        }
    except subprocess.TimeoutExpired:
        rec = {"file": spec["demo"], "exit": "timeout",
               "seconds": round(time.monotonic() - t0, 3),
               "npz_sha256": None, "stderr_tail": "timeout 180s"}
    if out.is_file():
        out.unlink()
    return rec


def release_hold() -> dict:
    tags = subprocess.run(["git", "tag", "-l", "v1.1*"], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()
    changelog = (REPO / "CHANGELOG.md").read_text()
    unreleased = "## Unreleased" in changelog
    published = bool(re.search(r"## \[1\.1\.0\] - \d{4}-", changelog))
    version = re.search(r'^version = "([^"]+)"',
                        (REPO / "Cargo.toml").read_text(), re.M).group(1)
    return {
        "v11_tags": tags.split() if tags else [],
        "changelog_unreleased": unreleased,
        "changelog_published": published,
        "workspace_version": version,
        "hold_standing": not tags and unreleased and not published,
    }


def main() -> int:
    record = {
        "schema": "actinv-p25b-g0-seal-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "opening_commit": OPENING_COMMIT,
        "prior_verdicts": {},
        "prior_evidence_sha256": {},
        "candidates": {},
        "demonstrations": {},
        "release_hold": release_hold(),
    }

    for name, want in VERDICTS.items():
        got = json.loads((RESULTS / name).read_text()).get("verdict")
        record["prior_verdicts"][name] = got
    for name in PRIOR_EVIDENCE:
        record["prior_evidence_sha256"][name] = sha256(RESULTS / name)
    for path in PRIOR_DOCS:
        record["prior_evidence_sha256"][path.name] = sha256(path)

    ok = True
    for corpus, spec in CORPORA.items():
        entries, census = build_manifest(spec["root"], spec["glob"])
        man_path = RESULTS / f"g0_p25b_manifest_{corpus}.txt"
        body = "".join(f"{h}  {rel}\n" for rel, h in sorted(entries.items()))
        man_path.write_text(body)
        blk = {
            "root": str(spec["root"]),
            "file_count": len(entries),
            "manifest": man_path.name,
            "manifest_sha256": sha256(man_path),
            "format_sniff_census": census,
        }
        if spec["provenance_file"] and spec["provenance_file"].is_file():
            blk["provenance_file_sha256"] = sha256(spec["provenance_file"])
        if spec["manifest_file"] and spec["manifest_file"].is_file():
            blk["upstream_manifest_sha256"] = sha256(spec["manifest_file"])
        record["candidates"][corpus] = blk
        demo = demonstration(corpus, spec)
        record["demonstrations"][corpus] = demo
        if demo["exit"] != 0:
            ok = False
        if census.get("format_unsupported") or census.get("unreadable"):
            pass  # census records counts; G1 decides gate impact

    record["prior_verdicts_ok"] = record["prior_verdicts"] == VERDICTS
    if not record["prior_verdicts_ok"] or not record["release_hold"]["hold_standing"]:
        ok = False
    record["pass"] = ok

    out = RESULTS / "g0_p25b_seals.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"],
                      "demos": {k: v["exit"] for k, v in
                                record["demonstrations"].items()},
                      "census": {k: v["format_sniff_census"] for k, v in
                                 record["candidates"].items()}},
                     indent=1, sort_keys=True))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
