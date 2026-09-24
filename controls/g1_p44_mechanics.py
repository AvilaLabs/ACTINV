#!/usr/bin/env python3
"""P44 G1 mechanics evidence: development-partition band production +
coverage score, recorded with band-record identities and per-point
completeness counts."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g1_p44_mechanics.json")
DATA = os.path.expanduser("~/nuclear-data")
WORK = os.path.join(DATA, "p44-work")
BANDS = os.path.join(WORK, "bands")

sys.path.insert(0, os.path.join(ROOT, "controls"))
import p44_bands  # noqa: E402


def sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    seal = json.load(open(os.path.join(ROOT, "results", "g0_p44_seals.json")))
    expected = sorted(tuple(e) for e in seal["partitions"]["development"])
    have = sorted(p44_bands.experiments().__iter__().__next__() and
                  [e for e in p44_bands.experiments()
                   if e in p44_bands.DEVELOPMENT])
    assert sorted(have) == expected, "development partition drifted"

    coverage_path = os.path.join(WORK, "coverage_development.json")
    subprocess.run(
        [sys.executable,
         os.path.join(ROOT, "controls", "p44_band_coverage.py"),
         "--partition", "development", "--out", coverage_path],
        check=True, capture_output=True, text=True)

    band_records = {}
    n_fo = n_sa = n_pts = 0
    for m, e in expected:
        p = os.path.join(BANDS, f"{m}__{e}.json")
        rec = json.load(open(p))
        fo, sa = rec["first_order"], rec["sampled"]
        band_records[f"{m}/{e}"] = {
            "record_sha256": sha(p),
            "first_order": {"status": fo["status"],
                            "n_bands": len(fo.get("bands") or [])},
            "sampled": {"status": sa["status"],
                        "n_bands": len(sa.get("bands") or []),
                        "n_samples": sa.get("n_samples")},
            "cooling_steps": len(rec.get("cooling_step_ends_s") or []),
        }
        n_fo += fo["status"] == "executed"
        n_sa += sa["status"] == "executed"
        n_pts += len(rec.get("cooling_step_ends_s") or [])

    cov = json.load(open(coverage_path))
    pooled = {k: v["pooled"]["all"] for k, v in cov["aggregates"].items()}
    complete = all(
        len((r["first_order"] or {}).get("bands") or []) ==
        len((r["sampled"] or {}).get("bands") or []) ==
        len(r.get("cooling_step_ends_s") or [])
        for r in (json.load(open(os.path.join(BANDS, f"{m}__{e}.json")))
                  for m, e in expected))

    doc = {
        "gate": "G1",
        "phase": "P44",
        "seal_sha256": sha(os.path.join(ROOT, "results",
                                        "g0_p44_seals.json")),
        "development_partition": [f"{m}/{e}" for m, e in expected],
        "band_records": band_records,
        "first_order_executed": n_fo,
        "sampled_executed": n_sa,
        "cooling_steps_total": n_pts,
        "coverage_path": coverage_path,
        "coverage_sha256": sha(coverage_path),
        "coverage_pooled": pooled,
        "n_scored_points": cov["n_points"],
        "per_point_records_complete": complete,
        "pass": (n_fo == n_sa == len(expected) and complete
                 and cov["n_points"] > 0),
    }
    json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(doc, indent=1))


if __name__ == "__main__":
    main()
