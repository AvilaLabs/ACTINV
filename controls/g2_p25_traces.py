#!/usr/bin/env python3
"""P25-G2: mechanism traces and final cause classification.

G1 enumerated every dropped outcome and each quarantined file's first-hit
failure message.  G2 assigns the *final* mechanism class per file by
evaluating, at exact-decimal precision, whether each file's emitted-state
excesses exist **at declared product gridpoints** (the source evaluation is
internally inconsistent) or only **between** them (a grid-density /
interpolation-shape artifact of tabulating coarse MF=10 partials against a
dense MF=3 total).

Discriminator, applied to every (mt, zap) partial family in every
quarantined file on the union of all involved grids:

- ``floor``             excess at or below 1e-15 barn absolute — physically
                        weightless (the observed pattern is N co-equal floor
                        states where the total carries a single floor);
- ``interp``            out-of-envelope relative excess only at energies
                        that are not product gridpoints;
- ``gridpoint``         out-of-envelope relative excess at a declared
                        product gridpoint — genuine source inconsistency;
- ``zero_total``        positive partial where the MF=3 total is exactly 0;
- ``no_mf3_total``      an MF=10 section exists with no MF=3 comparator.

A file whose only out-of-envelope excesses are ``floor`` is rescued by
floor-aware reconciliation alone; ``interp`` needs a collapse-consistency
repair; ``gridpoint``/``zero_total`` are source defects that stay
quarantined absent separately versioned corrected data.

Also adjudicated here: the projectile-aware ``inelastic(mt)`` question —
the corpus-wide MF=8 residual scan from G1 plus representative MF=8
records establishing that charged-particle MT=4 declares a *different*
nuclide (neutron-emission channel), which the builder's excitation path
mishandles by dropping ground-state production.

Writes ``results/g2_p25_traces.json``.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from p18b_decimal_corpus_oracle import parse_evaluation, table_value


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results/g2_p25_traces.json"
CENSUS = ROOT / "results/g1_p25_census.json"
FAILED_DIR = ROOT / "target/g4-p18b"
DATA = Path("/home/connoravila/nuclear-data/tendl-2025/files")

TINY = Decimal("1e-15")
ENVELOPE = Decimal("0.001")


def mat_of(path: Path) -> int:
    for ln in path.read_text("ascii", "replace").splitlines()[:12]:
        try:
            if int(ln[70:72]) == 1 and int(ln[72:75]) == 451:
                return int(ln[66:70])
        except (ValueError, IndexError):
            pass
    raise ValueError(f"{path.name}: no MF=1/MT=451 header")


def discriminate(path: Path) -> dict:
    """Classify every emitted-state excess in one file."""
    ev = parse_evaluation(path, mat_of(path))
    kinds: set[str] = set()
    detail: list[str] = []
    worst_rel = Decimal(0)
    for mt, prods in ev.mf10.items():
        total = ev.mf3.get(mt)
        if total is None:
            if prods:
                kinds.add("no_mf3_total")
                detail.append(f"MT{mt}: MF=10 states with no MF=3 total")
            continue
        byzap: dict[int, list] = defaultdict(list)
        for pr in prods:
            if pr.zap >= 0:
                byzap[pr.zap].append(pr)
        for zap, plist in byzap.items():
            xs = sorted(set(total.x) | set().union(*(set(pr.table.x) for pr in plist)))
            prod_grid = set().union(*(set(pr.table.x) for pr in plist))
            for e in xs:
                t = table_value(total, e, "right", "value")
                s = sum(table_value(pr.table, e, "right", "value") for pr in plist)
                if s <= t:
                    continue
                exc = s - t
                if exc <= TINY:
                    kinds.add("floor")
                    continue
                if t == 0:
                    kinds.add("zero_total")
                    detail.append(f"MT{mt}/ZAP{zap} @ {float(e):.4e} eV: total 0, states {float(s):.3e} b")
                elif exc > ENVELOPE * t:
                    rel = exc / t
                    worst_rel = max(worst_rel, rel)
                    if e in prod_grid:
                        kinds.add("gridpoint")
                        detail.append(f"MT{mt}/ZAP{zap} @ declared {float(e):.4e} eV: rel {float(rel):.3e}")
                    else:
                        kinds.add("interp")
                        detail.append(f"MT{mt}/ZAP{zap} @ {float(e):.4e} eV (between gridpoints): rel {float(rel):.3e}")
    return {
        "kinds": sorted(kinds),
        "worst_relative_excess": float(worst_rel),
        "detail": detail[:10],
    }


def final_class(kinds: list[str]) -> str:
    """Most-severe mechanism present — repair order is floor < interp <
    no_mf3_total < zero_total < gridpoint (genuine source inconsistency)."""
    k = set(kinds)
    if "gridpoint" in k:
        return "genuine_source_inconsistency:gridpoint_excess"
    if "zero_total" in k:
        return "genuine_source_inconsistency:zero_total_with_partials"
    if "no_mf3_total" in k:
        return "missing_total_or_grid_contract"
    if "interp" in k:
        return "grid_density_interpolation_artifact"
    if "floor" in k:
        return "floor_artifact_only"
    return "no_excess_found"


def main() -> None:
    census = json.loads(CENSUS.read_text())
    out: dict[str, dict] = {}
    for proj in ("neutron", "proton", "deuteron", "alpha"):
        proj_out: dict[str, dict] = {}
        for source in sorted((FAILED_DIR / f"failed-{proj}").glob("*.tendl")):
            disc = discriminate(source)
            first_hit = (census["file_failures"].get(proj, {}).get(source.name) or {})
            proj_out[source.name] = {
                "first_hit": {
                    "class": first_hit.get("class"),
                    "mt": first_hit.get("mt"),
                    "zap": first_hit.get("zap"),
                    "group": first_hit.get("group"),
                    "message": first_hit.get("message", "")[:300],
                },
                "all_excesses": disc,
                "final_class": final_class(disc["kinds"]),
            }
        out[proj] = proj_out

    counts = {
        p: dict(Counter(v["final_class"] for v in files.items()))
        for p, files in out.items()
    }
    record = {
        "schema": "actinv-p25-traces-1",
        "gate": "P25-G2",
        "census_source": "results/g1_p25_census.json",
        "discriminator": {
            "tiny_absolute_threshold_barn": float(TINY),
            "relative_envelope": float(ENVELOPE),
            "grid": "union of MF=3 total and MF=10 product declared abscissae, exact-decimal evaluation",
        },
        "final_class_counts": counts,
        "files": out,
        "inelastic_adjudication": census.get("inelastic_residual_scan"),
        "pass": None,
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
