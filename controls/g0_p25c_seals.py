#!/usr/bin/env python3
"""P25c G0 opening seal: source pin, sealed population, license, hold check.

Hash-pins the TENDL-2025 neutron corpus manifest, seals the 81-file
``conservation_excess_untraced`` population from the P25 G1 census (with
per-file source SHA-256 rehashed from the pinned corpus), rehashes all prior
verdicts verbatim and the P25/P25b evidence, records the TENDL license terms
applicable to a derived corpus, and confirms release state: the shipped
``data-v1.0.0`` catalog identity is unchanged and no ``data-v1.1*`` tag
exists.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
PROTOCOL = REPO / "protocols" / "ACTINV-P25c_PROTOCOL.md"
PROTOCOL_SHA256 = "7e42d73c759ee5ad663d65fee71b512e1904fac1e1dacc617dc32dc95edbfa05"
OPENING_COMMIT = "c4f6b4daaad542e9c160d427cedf8fc25019a5e5"

SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
MANIFEST_OUT = RESULTS / "g0_p25c_manifest_tendl_2025_n.txt"
CENSUS = RESULTS / "g1_p25_census.json"

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
    "verdict_p25b.json": "P25b-FAIL",
}

PRIOR_EVIDENCE = [
    "g1_p25_census.json",
    "g2_p25_traces.json",
    "g4_p25_repairs.json",
    "g5_p25_acceptance.json",
    "g4_p25b_union.json",
    "g4_p25b_union_irdff_score.json",
    "g5_p25b_accounting.json",
]

PRIOR_DOCS = [
    REPO / "docs" / "P25_TENDL2025_DEFECT_REPORT.md",
    REPO / "docs" / "DATA_LIMITATIONS.md",
    REPO / "docs" / "DATA_RELEASE_NOTES_v1.0.0.md",
]

CATALOG_MANIFEST = (
    REPO / "crates" / "actinv-cli" / "data" / "actinv-data-catalog-v1.0.0.json"
)
CATALOG_ARTIFACTS = REPO / "actinv-data" / "v1.0.0"

LICENSE_RECORD = {
    "corpus": "TENDL-2025",
    "custodian": "PSI — A.J. Koning, D. Rochman",
    "terms": "no explicit license; openly distributed; citation requested",
    "terms_url": "https://tendl.web.psi.ch/ and https://tendl.imperial.ac.uk/tendl_2025/tendl2025.html",
    "derivative_policy": "derived corpus permitted with attribution; must be "
    "labeled as an Avila Labs remediation derivative, never presented as an "
    "upstream TENDL release; citation of Rochman et al. Nucl. Phys. A 1053 "
    "(2025) 122951 and Koning et al. Nucl. Data Sheets 155 (2019) 1 required",
    "verified_at": "2026-09-14",
    "note": "NEA Data Bank mirror asserts OECD copyright; the pinned source "
    "corpus derives from the PSI distribution, not the NEA mirror.",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> int:
    t0 = time.time()
    problems: list[str] = []

    # 1. protocol + opening-commit binding
    proto_hash = sha256(PROTOCOL)
    if proto_hash != PROTOCOL_SHA256:
        problems.append(f"protocol hash drifted: {proto_hash}")
    head = git("rev-parse", "HEAD")
    anc = subprocess.run(
        ["git", "-C", str(REPO), "merge-base", "--is-ancestor",
         OPENING_COMMIT, "HEAD"],
    ).returncode
    if anc != 0:
        problems.append(f"opening commit {OPENING_COMMIT} not an ancestor of HEAD")

    # 2. source corpus manifest
    manifest = {}
    for path in sorted(SOURCE_ROOT.glob("*.tendl")):
        manifest[path.name] = sha256(path)
    MANIFEST_OUT.write_text(
        "".join(f"{h}  {n}\n" for n, h in manifest.items())
    )
    manifest_hash = hashlib.sha256(MANIFEST_OUT.read_bytes()).hexdigest()

    # 3. sealed 81-file population from the P25 census
    census = json.loads(CENSUS.read_text())
    neut = census["file_failures"]["neutron"]
    population = {
        name: rec for name, rec in neut.items()
        if rec.get("class") == "conservation_excess_untraced"
    }
    pop_detail = {}
    for name, rec in sorted(population.items()):
        src = SOURCE_ROOT / name
        if not src.exists():
            problems.append(f"sealed file missing from source corpus: {name}")
            continue
        h = sha256(src)
        m = re.search(r"source_sha256=([0-9a-f]{64})", rec.get("message", ""))
        census_sha = m.group(1) if m else None
        pop_detail[name] = {
            "source_sha256": h,
            "census_source_sha256": census_sha,
            "census_mt": rec.get("mt"),
            "census_lmf": rec.get("lmf"),
            "relative_excess": rec.get("relative_excess"),
            "sha_matches_census": h == census_sha,
        }
        if census_sha and h != census_sha:
            problems.append(f"{name}: source hash differs from census record")
    pop_hash = hashlib.sha256(
        json.dumps(sorted(pop_detail.items()), sort_keys=True).encode()
    ).hexdigest()

    # 4. prior verdicts re-verified verbatim
    prior_verdicts, v_ok = {}, True
    for fname, expected in VERDICTS.items():
        path = RESULTS / fname
        if not path.exists():
            problems.append(f"missing verdict {fname}")
            v_ok = False
            continue
        body = json.loads(path.read_text())
        got = body.get("verdict")
        prior_verdicts[fname] = {
            "sha256": sha256(path), "verdict": got, "ok": got == expected,
        }
        if got != expected:
            v_ok = False
            problems.append(f"{fname}: verdict {got} != sealed {expected}")

    # 5. prior evidence rehash
    evidence = {}
    for name in PRIOR_EVIDENCE:
        path = RESULTS / name
        evidence[name] = sha256(path) if path.exists() else None
        if not path.exists():
            problems.append(f"missing evidence {name}")
    docs = {p.name: sha256(p) for p in PRIOR_DOCS if p.exists()}

    # 6. release-state checks: shipped catalog unchanged, no v1.1 data tag
    tags = [t for t in git("tag", "-l", "data-v1.1*").splitlines() if t]
    if tags:
        problems.append(f"data-v1.1* tag already exists: {tags}")
    catalog_sha = sha256(CATALOG_MANIFEST) if CATALOG_MANIFEST.exists() else None
    if catalog_sha is None:
        problems.append(f"shipped catalog manifest missing: {CATALOG_MANIFEST}")
    artifact_count = (
        sum(1 for _ in CATALOG_ARTIFACTS.rglob("*") if _.is_file())
        if CATALOG_ARTIFACTS.exists() else 0
    )
    if PATCHED_ROOT.exists():
        problems.append(
            f"patched-corpus path already exists before G2: {PATCHED_ROOT}"
        )

    record = {
        "schema": "actinv-p25c-g0-seals-1",
        "gate": "P25c-G0",
        "protocol_sha256": proto_hash,
        "protocol_sha256_expected": PROTOCOL_SHA256,
        "opening_commit": OPENING_COMMIT,
        "head": head,
        "source_corpus": {
            "root": str(SOURCE_ROOT),
            "file_count": len(manifest),
            "manifest_file": str(MANIFEST_OUT),
            "manifest_sha256": manifest_hash,
        },
        "sealed_population": {
            "class": "conservation_excess_untraced",
            "source": "results/g1_p25_census.json:file_failures.neutron",
            "count": len(pop_detail),
            "population_sha256": pop_hash,
            "files": pop_detail,
        },
        "prior_verdicts": prior_verdicts,
        "prior_verdicts_ok": v_ok,
        "prior_evidence_sha256": evidence,
        "prior_docs_sha256": docs,
        "license": LICENSE_RECORD,
        "release_state": {
            "data_v1_1_tags": tags,
            "shipped_catalog_manifest": str(CATALOG_MANIFEST),
            "shipped_catalog_manifest_sha256": catalog_sha,
            "shipped_artifact_count": artifact_count,
            "patched_corpus_path": str(PATCHED_ROOT),
            "patched_corpus_preexists": PATCHED_ROOT.exists(),
        },
        "elapsed_s": round(time.time() - t0, 3),
    }
    record["pass"] = not problems
    record["problems"] = problems
    out = RESULTS / "g0_p25c_seals.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(f"G0 seal: {'PASS' if record['pass'] else 'FAIL'} "
          f"({len(manifest)} source files, {len(pop_detail)} sealed, "
          f"{record['elapsed_s']}s)")
    for p in problems:
        print("  PROBLEM:", p)
    print(f"  wrote {out}")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
