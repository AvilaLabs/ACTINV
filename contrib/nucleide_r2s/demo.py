#!/usr/bin/env python3
"""ACTINV -> ALARA photon source -> nucleide R2S round-trip demo (P102).

Runs ``actinv export-source alara`` on an ``actinv-r2s-source-1`` document,
loads each emitted ``.photonSrc`` with nucleide's ALARA photon-source reader,
builds nucleide zone photon sources, tags voxels through nucleide's ``r2s``
API, and prints a conservation table comparing Sigma_g density * volume with
each cell's declared ``photons_s``.

Usage::

    python3 contrib/nucleide_r2s/demo.py [R2S_SOURCE.ndjson]
        [--shutdown-t-s T] [--out DIR]

Defaults to ``results/p52_r2s_source.ndjson`` and shutdown at 300 s, the
corpus's end of irradiation (300 s irradiation followed by 1 d, 30 d, and 1 y
cooling steps). Requires ``nucleide==0.16.0`` and an ``actinv`` binary on
``PATH`` or at ``target/release/actinv`` / ``target/debug/actinv``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def find_actinv() -> str:
    if os.environ.get("ACTINV_BIN"):
        return os.environ["ACTINV_BIN"]
    for candidate in (ROOT / "target/release/actinv", ROOT / "target/debug/actinv"):
        if candidate.exists():
            return str(candidate)
    on_path = shutil.which("actinv")
    if on_path:
        return on_path
    raise FileNotFoundError(
        "no actinv binary found — build with `cargo build --release -p actinv-cli` "
        "or install it on PATH"
    )


def _temporary_root() -> Path:
    root = Path(os.environ.get("TMPDIR", ROOT / "target/preflight-tmp")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_demo(source: Path, shutdown_t_s: float, out_dir: Path, actinv_bin: str) -> int:
    """Export one source, read it through Nucleide, and print conservation."""
    from nucleide import alara, r2s

    out_dir = out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} exists and is not empty")

    result = subprocess.run(
        [actinv_bin, "export-source", "alara", str(source), str(out_dir),
         "--shutdown-t-s", str(shutdown_t_s)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print(result.stderr.strip())  # actinv's own one-line summary

    index = json.loads((out_dir / "actinv-alara-index.json").read_text())
    cooling = index["cooling_s"]
    print(f"cooling time: {index['time_token']} (step_t_s={index['step_t_s']}, "
          f"shutdown_t_s={index['shutdown_t_s']})")
    print(f"{len(index['cells'])} cells, group grid length "
          f"{len(index['group_centroids_eV'])}")
    print()

    # Each mesh cell is treated as one independent volume element. Nucleide's
    # tagging API accepts whole-zone strength; the photon file stores density.
    zone_strengths = []
    rows = []
    for entry in index["cells"]:
        path = out_dir / entry["file"]
        text = path.read_text()
        sums = r2s.photon_group_sums(text, ["TOTAL"], cooling)
        total_strength = alara.alara_photon_total_strength(text)
        reconstructed = total_strength * entry["volume_cm3"]
        zone_strengths.append(reconstructed)
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

    tag = r2s.tag_zone_strength(zone_strengths, list(range(len(zone_strengths))), split=False)

    print(f"{'id':<20}{'declared photons/s':>22}{'reconstructed':>18}{'rel. error':>14}")
    for row in rows:
        print(f"{row['id']:<20}{row['declared_photons_s']:>22.6g}"
              f"{row['reconstructed_photons_s']:>18.6g}{row['relative_error']:>14.2e}")

    total_declared = sum(row["declared_photons_s"] for row in rows)
    print()
    print(f"total declared photons/s: {total_declared:.6g}")
    print(f"nucleide r2s.tag_zone_strength total: {tag['total']:.6g}")
    max_error = max(row["relative_error"] for row in rows)
    print(f"max relative error: {max_error:.2e}")

    tag_error = abs(tag["total"] - total_declared) / max(1.0, abs(total_declared))
    return 0 if max(max_error, tag_error) <= 1e-9 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=str(ROOT / "results/p52_r2s_source.ndjson"),
                        help="actinv-r2s-source-1 NDJSON document")
    parser.add_argument("--shutdown-t-s", type=float, default=300.0,
                        help="shutdown reference time (seconds); cooling = step_t_s - this value")
    parser.add_argument("--out", default=None,
                        help="output directory (default: a temporary directory removed on exit)")
    args = parser.parse_args()

    try:
        actinv_bin = find_actinv()
        if args.out is not None:
            return run_demo(Path(args.source), args.shutdown_t_s, Path(args.out), actinv_bin)
        with tempfile.TemporaryDirectory(prefix="nucleide-r2s-", dir=_temporary_root()) as temporary:
            return run_demo(Path(args.source), args.shutdown_t_s, Path(temporary), actinv_bin)
    except ModuleNotFoundError as exc:
        if exc.name == "nucleide":
            print(f"nucleide not importable: {exc}", file=sys.stderr)
            print("install with: pip install nucleide==0.16.0", file=sys.stderr)
        else:
            print(f"nucleide import failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        print(f"nucleide/ACTINV demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
