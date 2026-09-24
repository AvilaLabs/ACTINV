#!/usr/bin/env python3
"""P46 G0 seal — binds protocol sha, opening commit, corpus identities
(sha + declared builder provenance), FNS manifest sha, decay data,
scorer/driver/report code shas and frozen constants before any score
exists. Emits results/g0_p46_seals.json.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402
import p46_corpora as p46c  # noqa: E402
import p46_score as p46s  # noqa: E402

OUT = ROOT / "results/g0_p46_seals.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=ROOT).stdout.strip()

    # FNS manifest: ordered (path, sha) list over the consumed files
    manifest = []
    fns = p44_bands.FNS
    for d in sorted(fns.iterdir()):
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix in (".i", ".exp", "_fluxes") or \
                    f.name.endswith("_fluxes"):
                manifest.append(
                    {"path": str(f.relative_to(fns)),
                     "sha256": sha256(f)})
    msha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()).hexdigest()

    corpora = {c: p46c.corpus_meta(c) for c in p46c.CORPORA}
    indexes = {c: str(p) for c, p in p46s.INDEX_PATHS.items()}
    for c, p in indexes.items():
        pp = Path(p)
        if not pp.is_file():
            raise SystemExit(f"missing index for {c}: {p}")

    code = {
        "driver": sha256(ROOT / "controls/p46_corpora.py"),
        "scorer": sha256(ROOT / "controls/p46_score.py"),
        "report": sha256(ROOT / "controls/p46_report.py"),
        "p44_bands": sha256(ROOT / "controls/p44_bands.py"),
        "p44_band_coverage": sha256(
            ROOT / "controls/p44_band_coverage.py"),
        "corpus_reader": sha256(
            ROOT / "controls/harness/fispact_io.py"),
    }
    seal = {
        "schema": "actinv-p46-seal-1",
        "sealed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                       time.gmtime()),
        "opening_commit": commit,
        "protocol_sha256": sha256(
            ROOT / "protocols/ACTINV-P46_PROTOCOL.md"),
        "corpora": corpora,
        "corpus_indexes": {c: {"path": p,
                               "sha256": sha256(Path(p))}
                           for c, p in indexes.items()},
        "fns_manifest_sha256": msha,
        "fns_files": len(manifest),
        "decay": {"primary": str(p46c.DECAY_PRIMARY),
                  "primary_sha256": sha256(p46c.DECAY_PRIMARY),
                  "fallback": str(p46c.DECAY_FALLBACK),
                  "fallback_sha256": sha256(p46c.DECAY_FALLBACK)},
        "code_sha256": code,
        "binary_sha256": sha256(ROOT / "target/release/actinv"),
        "constants": {
            "within20": list(p46s.WITHIN20),
            "within2x": list(p46s.WITHIN2X),
            "min_points_for_rec": p46s.MIN_POINTS_FOR_REC,
            "min_corpora_for_rec": p46s.MIN_CORPORA_FOR_REC,
            "tie_tol": p46s.TIE_TOL,
            "development_subset": sorted(
                f"{m}/{e}" for m, e in p46c.DEVELOPMENT),
            "irdff_arm": "unmeasured",
        },
    }
    OUT.write_text(json.dumps(seal, indent=1))
    print(json.dumps({"sealed": True, "commit": commit[:12],
                      "manifest_files": len(manifest),
                      "corpora": {c: v["sha256"][:12]
                                  for c, v in corpora.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
