#!/usr/bin/env python3
"""ACTINV -> ALARA photon source -> nucleide R2S round-trip demo (P102).

Runs ``actinv export-source alara`` on an ``actinv-r2s-source-1`` document,
loads every emitted ``.photonSrc`` file with nucleide's own ALARA photon-
source reader, builds nucleide zone photon sources, tags voxels through
nucleide's ``r2s`` API, and prints a conservation table: each cell's
Sigma_g density * volume against the cell's declared ``photons_s``.

Usage:

    python3 contrib/nucleide_r2s/demo.py [R2S_SOURCE.ndjson] [--shutdown-t-s T]

Defaults to the repository's corpus document
(``results/p52_r2s_source.ndjson``) and ``--shutdown-t-s 0`` (shutdown).
Requires ``nucleide`` importable (``pip install nucleide==0.16.0``) and an
``actinv`` binary on ``PATH`` or at ``target/release/actinv`` /
``target/debug/actinv`` relative to the repository root.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def find_actinv() -> str:
    for candidate in (ROOT / "target/release/actinv", ROOT / "target/debug/actinv"):
        if candidate.exists():
            return str(candidate)
    on_path = shutil.which("actinv")
    if on_path:
        return on_path
    raise SystemExit(
        "no actinv binary found — build with `cargo build --release -p actinv-cli` "
        "or install it on PATH")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?",
                        default=str(ROOT / "results/p52_r2s_source.ndjson"),
                        help="actinv-r2s-source-1 NDJSON document")
    parser.add_argument("--shutdown-t-s", type=float, default=0.0,
                        help="shutdown reference time (seconds); cooling = "
                             "step_t_s - this value")
    parser.add_argument("--out", default=None,
                        help="output directory (default: a throwaway temp dir)")
    args = parser.parse_args()

    try:
        from nucleide import alara, r2s
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"nucleide not importable: {exc}")
        print("install with: pip install nucleide==0.16.0")
        return 1

    actinv_bin = find_actinv()
    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="nucleide-r2s-"))
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"{out_dir} exists and is not empty")

    r = subprocess.run(
        [actinv_bin, "export-source", "alara", args.source, str(out_dir),
         "--shutdown-t-s", str(args.shutdown_t_s)],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        return 1
    print(r.stderr.strip())  # actinv's own one-line summary

    index = json.loads((out_dir / "actinv-alara-index.json").read_text())
    cooling = index["cooling_s"]
    print(f"cooling time: {index['time_token']} (step_t_s={index['step_t_s']}, "
          f"shutdown_t_s={index['shutdown_t_s']})")
    print(f"{len(index['cells'])} cells, group grid length "
          f"{len(index['group_centroids_eV'])}")
    print()

    # Load every file with nucleide's own reader, build zone photon sources,
    # and tag voxels through nucleide's r2s API (one voxel == one zone here,
    # since the mesh cells are already independent volume elements).
    zone_strengths = []
    rows = []
    for entry in index["cells"]:
        path = out_dir / entry["file"]
        text = path.read_text()
        sums = r2s.photon_group_sums(text, ["TOTAL"], cooling)
        total_strength = alara.alara_photon_total_strength(text)
        zone_strengths.append(total_strength)
        reconstructed = total_strength * entry["volume_cm3"]
        declared = entry["photons_s"]
        denom = max(1.0, abs(declared))
        rows.append({
            "id": entry["id"],
            "file": entry["file"],
            "groups_read": len(sums["sums"]),
            "declared_photons_s": declared,
            "reconstructed_photons_s": reconstructed,
            "relative_error": abs(reconstructed - declared) / denom,
        })

    tag = r2s.tag_zone_strength(zone_strengths, list(range(len(zone_strengths))),
                                split=False)

    print(f"{'id':<20}{'declared photons/s':>22}{'reconstructed':>18}{'rel. error':>14}")
    for row in rows:
        print(f"{row['id']:<20}{row['declared_photons_s']:>22.6g}"
              f"{row['reconstructed_photons_s']:>18.6g}{row['relative_error']:>14.2e}")

    total_declared = sum(r["declared_photons_s"] for r in rows)
    print()
    print(f"total declared photons/s: {total_declared:.6g}")
    print(f"nucleide r2s.tag_zone_strength total: {tag['total']:.6g}")
    print(f"max relative error: {max(r['relative_error'] for r in rows):.2e}")

    if args.out is None:
        shutil.rmtree(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
