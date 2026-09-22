#!/usr/bin/env python3
"""P42 G0 seal: bind the protocol, P40 record, artifact and census case dirs.

Produces results/g0_p42_seals.json. No numerical content from the case
dirs enters the record -- only identities and digests.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
CASE_ROOT = Path.home() / "nuclear-data" / "p26b-work" / "p38-run" / "g2-cases-p39"
ARTIFACT = Path.home() / "nuclear-data" / "p26b-work" / "p38-run" / "actinv_fendl32c_709_p39.npz"

EVIDENCE_FILES = ("out.json", "case.stdout", "alara.dmp")

OPEN_CLASSES = {
    "short_lived_products_absent_in_actinv": ["Ti55", "V55", "Ti53", "Cr57", "Mn59"],
    "common_nuclide_magnitude": [
        "Cr51", "Fe59", "Cr55", "Mn57", "Fe53", "Cr56", "Mn58", "V52"
    ],
}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> None:
    protocol = ROOT / "protocols" / "ACTINV-P42_PROTOCOL.md"
    verdict40 = RES / "verdict_p40.json"
    seals40 = RES / "g0_p40_seals.json"

    cases = sorted(p for p in CASE_ROOT.iterdir() if p.is_dir())
    tree = hashlib.sha256()
    n_files = 0
    missing = []
    for cdir in cases:
        for name in EVIDENCE_FILES:
            f = cdir / name
            if not f.is_file():
                missing.append(f"{cdir.name}/{name}")
                continue
            tree.update(cdir.name.encode())
            tree.update(name.encode())
            tree.update(sha(f).encode())
            n_files += 1

    v40 = json.load(open(verdict40))
    s40 = json.load(open(seals40))

    seals = {
        "schema": "actinv-p42-g0-1",
        "phase": "P42",
        "protocol": "protocols/ACTINV-P42_PROTOCOL.md",
        "protocol_sha256": sha(protocol),
        "sealed_at_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip(),
        "prior_verdicts": {
            "verdict_p40.json": v40["verdict"],
            "verdict_p40_sha256": sha(verdict40),
        },
        "identities": {
            "actinv_fendl32c_artifact": {
                "path": str(ARTIFACT),
                "sha256": sha(ARTIFACT),
                "expected_sha256": s40["artifact_sha256"],
            },
        },
        "census": {
            "case_root": str(CASE_ROOT),
            "case_count": len(cases),
            "evidence_files": list(EVIDENCE_FILES),
            "files_hashed": n_files,
            "missing_files": missing,
            "tree_sha256": tree.hexdigest(),
            "p40_census_sha256": s40["census_sha256"],
        },
        "open_classes": {
            k: {"members": v, "source": "verdict_p40.json divergence_classes"}
            for k, v in OPEN_CLASSES.items()
        },
        "frozen_vocabulary": [
            "coverage_parent", "decay_file_gap", "decay_feed_missing",
            "burn_out", "conversion_content", "alara_heritage",
            "true_defect", "unresolved",
        ],
        "expectations": [
            "every member nuclide of both open classes lands in exactly one frozen class with a hash-pinned trace",
            "repairs are permitted only for true_defect / conversion_content mechanisms",
            "the extended artifact, if built, is labeled extended_artifact and derived from case inputs alone",
            "no value is tuned toward ALARA's output; unresolved is a valid close, omission is not",
        ],
    }
    out = RES / "g0_p42_seals.json"
    json.dump(seals, open(out, "w"), indent=1, sort_keys=True)
    print(f"wrote {out} ({len(cases)} cases, {n_files} files, "
          f"{len(missing)} missing)")


if __name__ == "__main__":
    main()
