#!/usr/bin/env python3
"""P20 G0 producer: complete MF=33 census of the frozen TENDL-2025 neutron
corpus plus the Core-case sensitivity-mass coverage fraction.

Runs the ``p20_corpus_probe`` binary (same ``parse_mf33`` path the covariance
builder uses) over every ``n-*.tendl`` file, aggregates the per-(ZA,LISO,MT)
block inventory, measures the energy overlap of covariance grids against the
MF=3 reaction domains, and joins the frozen Core case's sensitivity ledger to
report the covered sensitivity-mass fraction.

Writes ``results/g0_p20_mf33_census.json.gz``.  Read-only over the corpus.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g4_p18b_diagnostics as g4  # noqa: E402  (ENDF walkers + MF3 domains)

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "results/g0_p20_mf33_census.json.gz"
PROBE = Path(
    os.environ.get("ACTINV_P20_PROBE", ROOT / "target/release/p20_corpus_probe")
)
PROTOCOL = ROOT / "protocols/ACTINV-P20_PROTOCOL.md"
CORE_RESULT = Path(
    os.environ.get("ACTINV_P20_CORE_RESULT", ROOT / "target/p20-core/result.json")
)
COV_INDEX = Path(
    os.environ.get(
        "ACTINV_P20_COV_INDEX", ROOT / "target/p11-full-v2-repro.cov_index.json"
    )
)
CHUNK = 64


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_files(paths: list[Path]) -> list[dict]:
    out: list[dict] = []
    for start in range(0, len(paths), CHUNK):
        chunk = paths[start : start + CHUNK]
        completed = subprocess.run(
            [str(PROBE), *[str(p) for p in chunk]],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"corpus probe failed on chunk {start}: {completed.stderr[-2000:]}"
            )
        out.extend(json.loads(completed.stdout))
    return out


def self_block_overlap(rec: dict, domains: dict[int, tuple[float, float]]):
    """Per self-block (mt == mt1), the log-energy overlap fraction of the
    covariance row grid against the MF=3 domain of the same MT.  Returns a
    list of per-mt rows; MTs absent from the MF=3 domain table are skipped
    (they carry no activation row)."""
    by_mt: dict[int, list[dict]] = defaultdict(list)
    for comp in rec["components"]:
        if comp["mt"] == comp["mt1"]:
            by_mt[comp["mt"]].append(comp)
    rows = []
    for mt, dom in sorted(domains.items()):
        d_lo, d_hi = dom
        if not (math.isfinite(d_lo) and math.isfinite(d_hi)) or d_hi <= d_lo:
            continue
        comps = by_mt.get(mt, [])
        if not comps:
            rows.append({"mt": mt, "cov_present": False, "overlap": 0.0})
            continue
        c_lo = min(c["row_e_lo"] for c in comps)
        c_hi = max(c["row_e_hi"] for c in comps)
        lo, hi = max(c_lo, d_lo), min(c_hi, d_hi)
        width = max(0.0, math.log(hi / lo)) if hi > lo > 0 else 0.0
        denom = math.log(d_hi / d_lo) if d_lo > 0 else math.nan
        rows.append(
            {
                "mt": mt,
                "cov_present": True,
                "overlap": width / denom if denom and denom > 0 else None,
                "domain_lo": d_lo,
                "domain_hi": d_hi,
                "cov_lo": c_lo,
                "cov_hi": c_hi,
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument(
        "--quick",
        type=int,
        default=0,
        metavar="N",
        help="scan only the first N corpus files (dev smoke)",
    )
    args = ap.parse_args()

    corpus_dir = g4.CORPUS / g4.MANIFEST_CODE["neutron"]
    files = sorted(corpus_dir.glob("*.tendl"))
    if not files:
        raise SystemExit(f"no corpus files under {corpus_dir}")
    if args.quick:
        files = files[: args.quick]
    print(f"census over {len(files)} neutron files...", flush=True)
    records = probe_files(files)

    parse_failures = []
    blocks = defaultdict(
        lambda: {
            "components": 0,
            "kinds": defaultdict(int),
            "lbs": defaultdict(int),
            "row_e_lo": math.inf,
            "row_e_hi": 0.0,
        }
    )
    lb_totals: dict[str, int] = defaultdict(int)
    kind_totals: dict[str, int] = defaultdict(int)
    files_with = total_components = total_sections = 0
    overlap_rows = []
    domain_failures = []

    for rec in records:
        name = Path(rec["file"]).name
        if rec["parse_error"] is not None:
            parse_failures.append({"file": name, "error": rec["parse_error"]})
        else:
            if rec["components"]:
                files_with += 1
            total_sections += rec["sections"]
            total_components += len(rec["components"])
            for comp in rec["components"]:
                key = f"{rec['za']},{rec['liso']},{comp['mt']},{comp['mt1']}"
                blk = blocks[key]
                blk["components"] += 1
                blk["kinds"][comp["kind"]] += 1
                blk["lbs"][str(comp["lb"])] += 1
                blk["row_e_lo"] = min(blk["row_e_lo"], comp["row_e_lo"])
                blk["row_e_hi"] = max(blk["row_e_hi"], comp["row_e_hi"])
                lb_totals[str(comp["lb"])] += 1
                kind_totals[comp["kind"]] += 1
        try:
            doms = g4.mf3_domains(Path(rec["file"]))
            overlap_rows.extend(
                {
                    "file": name,
                    "za": rec["za"],
                    "liso": rec["liso"],
                    **row,
                }
                for row in self_block_overlap(rec, doms)
            )
        except Exception as error:  # noqa: BLE001 — ledger the failure
            domain_failures.append({"file": name, "error": str(error)[:200]})

    covered = [r for r in overlap_rows if r.get("cov_present")]
    full_cover = [r for r in covered if (r.get("overlap") or 0.0) >= 0.999]
    core = None
    if CORE_RESULT.is_file():
        data = json.loads(CORE_RESULT.read_text())
        step = data["steps"][-1].get("uncertainty")
        if step:
            all_s = [
                abs(s["value"])
                for resp in step["responses"].values()
                for s in resp["sensitivities"]
            ]
            covered_mass = sum(
                abs(s["value"])
                for resp in step["responses"].values()
                for s in resp["sensitivities"]
                if s["parameter"].get("covariance_covered", True)
            )
            core = {
                "result_sha256": sha256(CORE_RESULT),
                "sensitivity_mass_total": sum(all_s),
                "sensitivity_mass_covered": covered_mass,
                "sensitivity_mass_fraction": (
                    covered_mass / sum(all_s) if all_s else None
                ),
                "uncovered_library_rows": step.get("uncovered_library_rows"),
                "absent_cross_parameter_pairs": step.get(
                    "absent_cross_parameter_pairs"
                ),
            }
    report = {
        "schema": "actinv-p20-g0-census-1",
        "protocol_sha256": sha256(PROTOCOL),
        "corpus": {
            "root": str(g4.CORPUS),
            "manifest_code": g4.MANIFEST_CODE["neutron"],
        },
        "covariance_index_sha256": sha256(COV_INDEX)
        if COV_INDEX.is_file()
        else None,
        "probe_sha256": sha256(PROBE),
        "census": {
            "files": len(records),
            "files_with_mf33": files_with,
            "mf33_sections": total_sections,
            "components": total_components,
            "parse_failures": parse_failures,
            "lb_counts": dict(lb_totals),
            "kind_counts": dict(kind_totals),
            "blocks": dict(sorted(blocks.items())),
        },
        "energy_overlap": {
            "self_block_rows": len(overlap_rows),
            "cov_present_rows": len(covered),
            "full_domain_cover_rows": len(full_cover),
            "mean_overlap": (
                sum(r["overlap"] for r in covered) / len(covered)
                if covered
                else None
            ),
            "domain_failures": domain_failures,
            "rows": overlap_rows,
        },
        "core_case": core,
    }
    with gzip.open(args.report, "wt") as stream:
        json.dump(report, stream, indent=1)
    print(
        f"files={len(records)} with_mf33={files_with} "
        f"sections={total_sections} components={total_components} "
        f"parse_failures={len(parse_failures)} "
        f"overlap_rows={len(overlap_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
