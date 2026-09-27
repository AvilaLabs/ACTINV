#!/usr/bin/env python3
"""P63 calibration instrument — reads a P44-format coverage document
(`actinv-p44-coverage-1`), re-derives pooled coverage under a grid of
declared unmodeled-relative scales u using the emitted band's own
quadrature fold

    covered(u) iff |measured - center| <= sqrt(w^2 + (z*u*n)^2 + sigma_m^2)

where w is the emitted half-width, c the band center, n the nominal,
z the document's normal multiplier, and sigma_m the point's measurement
sigma — the sealed P44 combined_sigma rule with the declared term folded
in quadrature exactly as the emitted band does. Emits
`actinv-calibration-1`: the coverage curve, the u at which pooled
coverage first reaches --target, per-material splits, and the class
of points no relative term can cover.

    p63_calibrate.py COVERAGE_DOC [--out CERT.json] [--target 0.68]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

SCHEMA = "actinv-calibration-1"
# normal multiplier at confidence 0.95: Phi^-1(0.975)
Z_95 = 1.959963984540054


def coverage_at(points: list, u: float, z: float) -> float | None:
    covered = 0
    for p in points:
        w = (p["band"]["hi"] - p["band"]["lo"]) / 2.0
        c = (p["band"]["hi"] + p["band"]["lo"]) / 2.0
        n = p["band"]["nominal"]
        # sealed-rule quadrature: |m - c| <= sqrt(w^2 + (z*u*n)^2 + s_m^2)
        half = math.sqrt(w * w + (z * u * n) ** 2 + p["sigma_W_g"] ** 2)
        if abs(p["measured_W_g"] - c) <= half:
            covered += 1
    return covered / len(points) if points else None


def extract(doc: dict) -> tuple[list, list]:
    finite, uncoverable = [], []
    for exp in doc["experiments"]:
        for p in exp["points"]:
            fo = p["bands"]["first_order"]
            b = fo.get("band")
            if (not isinstance(b, dict)
                    or not isinstance(b.get("nominal"), (int, float))
                    or b["nominal"] <= 0.0):
                uncoverable.append({"material": exp["material"],
                                    "experiment": exp["experiment"],
                                    "t_s": p["t_s"],
                                    "reason": fo.get("reason")})
                continue
            finite.append({"material": exp["material"],
                           "experiment": exp["experiment"],
                           "measured_W_g": p["measured_W_g"],
                           "sigma_W_g": p["sigma_W_g"],
                           "band": b})
    return finite, uncoverable


def u_at_target(points: list, target: float, z: float) -> float | None:
    """Bisection on the monotone coverage(u) curve; None if u=4 fails."""
    lo, hi = 0.0, 4.0
    if coverage_at(points, hi, z) < target:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if coverage_at(points, mid, z) < target:
            lo = mid
        else:
            hi = mid
    return hi


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("coverage_doc", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--target", type=float, default=0.68)
    ap.add_argument("--z", type=float, default=Z_95)
    args = ap.parse_args()

    raw = args.coverage_doc.read_bytes()
    doc = json.loads(raw)
    finite, uncoverable = extract(doc)
    total = len(finite) + len(uncoverable)

    grid = [0.0] + [round(0.05 * i, 4) for i in range(1, 81)]
    curve = {f"{u:.4g}": {
        "coverage_finite": coverage_at(finite, u, args.z),
        "coverage_all_points": ((coverage_at(finite, u, args.z) or 0.0)
                                * len(finite) / total if total else None),
    } for u in grid}
    u_star = u_at_target(finite, args.target, args.z)

    per_material: dict[str, dict] = {}
    for mat in sorted({p["material"] for p in finite}
                      | {p["material"] for p in uncoverable}):
        mf = [p for p in finite if p["material"] == mat]
        mu = [p for p in uncoverable if p["material"] == mat]
        per_material[mat] = {
            "finite_points": len(mf),
            "uncoverable_points": len(mu),
            "coverage_u0": coverage_at(mf, 0.0, args.z),
            "coverage_at_u_target": (coverage_at(mf, u_star, args.z)
                                     if u_star is not None else None),
        }

    cert = {
        "schema": SCHEMA,
        "input": {"path": str(args.coverage_doc),
                  "sha256": hashlib.sha256(raw).hexdigest(),
                  "schema": doc.get("schema"),
                  "n_experiments": doc.get("n_experiments"),
                  "n_points": doc.get("n_points")},
        "normal_multiplier": args.z,
        "target_coverage": args.target,
        "n_finite_bandable": len(finite),
        "n_uncoverable_by_relative_term": len(uncoverable),
        "uncoverable_reasons": {
            r: sum(1 for p in uncoverable if p["reason"] == r)
            for r in sorted({p["reason"] for p in uncoverable})
        },
        "u_at_target_finite": u_star,
        "coverage_curve": curve,
        "per_material": per_material,
        "statement": (
            f"On the sealed corpus ({len(finite)} bandable of {total} measured "
            f"points), modeled-band coverage at declared confidence is "
            f"{curve['0']['coverage_finite']:.3f}; reaching {args.target:.2f} "
            f"coverage over bandable points requires a declared unmodeled "
            f"relative term u={u_star if u_star is not None else '>4.0'} "
            f"(fitted, not derived). {len(uncoverable)} points are uncoverable "
            f"by any relative term (missing channels / unaligned steps)."
        ),
    }
    text = json.dumps(cert, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    print(text if not args.out else
          json.dumps({"wrote": str(args.out), "u_at_target": u_star,
                      "coverage_u0": curve["0"]["coverage_finite"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
