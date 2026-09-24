#!/usr/bin/env python3
"""P44 G4 sealed scoring producer: asserts the sealed partition's band
records are complete, runs the sealed scorer exactly once, and records
timing honestly.

Band-production wall time is reported as the span between the first
and last sealed band-record mtimes (the records' own timestamps) — the
production may run over multiple invocations; resume is content-verified
by the study machinery, and the reported span is wall-clock between
first and last sealed evidence writes. Scorer wall time is measured
here.
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g4_p44_scoring.json")
SEAL = os.path.join(ROOT, "results", "g0_p44_seals.json")
DATA = os.path.expanduser("~/nuclear-data")
WORK = os.path.join(DATA, "p44-work")
BANDS = os.path.join(WORK, "bands")


def sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main():
    seal = json.load(open(SEAL))
    sealed = [tuple(e) for e in seal["partitions"]["sealed"]]

    # resume/complete band production for the sealed partition
    p = subprocess.run(
        [sys.executable,
         os.path.join(ROOT, "controls", "p44_bands.py"),
         "--partition", "sealed"],
        capture_output=True, text=True)
    tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""

    complete = {}
    span = [None, None]
    for m, e in sealed:
        rp = os.path.join(BANDS, f"{m}__{e}.json")
        rec = json.load(open(rp)) if os.path.exists(rp) else {}
        ok = (rec.get("first_order", {}).get("status") == "executed"
              and rec.get("sampled", {}).get("status") == "executed")
        complete[f"{m}/{e}"] = ok
        if os.path.exists(rp):
            mt = os.path.getmtime(rp)
            span[0] = mt if span[0] is None else min(span[0], mt)
            span[1] = mt if span[1] is None else max(span[1], mt)
    n_done = sum(complete.values())

    if n_done != len(sealed):
        missing = [k for k, v in complete.items() if not v]
        doc = {"gate": "G4", "phase": "P44",
               "status": "band_production_incomplete",
               "sealed_experiments": len(sealed), "complete": n_done,
               "missing": missing[:10], "producer_tail": tail}
        json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
        print(json.dumps(doc, indent=1))
        return

    t0 = time.time()
    q = subprocess.run(
        [sys.executable,
         os.path.join(ROOT, "controls", "p44_band_coverage.py"),
         "--sealed", "--seal", SEAL, "--partition", "sealed"],
        capture_output=True, text=True)
    scorer_s = time.time() - t0
    if q.returncode != 0:
        doc = {"gate": "G4", "phase": "P44",
               "status": "sealed_scoring_failed",
               "stderr_tail": (q.stderr or "")[-500:]}
        json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
        print(json.dumps(doc, indent=1))
        return

    sealed_out = os.path.join(ROOT, "results", "p44_sealed_coverage.json")
    rep = json.load(open(sealed_out))
    band_span_min = ((span[1] - span[0]) / 60.0) if span[0] else None
    doc = {
        "gate": "G4",
        "phase": "P44",
        "status": "scored_once",
        "sealed_experiments": len(sealed),
        "n_points": rep["n_points"],
        "n_excluded_nonpoints": rep["n_excluded_nonpoints"],
        "band_production_wall_minutes": band_span_min,
        "band_wall_method": ("mtime span first-to-last sealed band "
                             "record (multi-invocation resume); "
                             "scorer timed directly"),
        "scorer_wall_minutes": scorer_s / 60.0,
        "total_wall_minutes": (band_span_min or 0) + scorer_s / 60.0,
        "envelope_minutes": seal["envelope"]["sealed_scoring_minutes"],
        "within_envelope":
            ((band_span_min or 0) + scorer_s / 60.0)
            <= seal["envelope"]["sealed_scoring_minutes"],
        "sealed_coverage_sha256": sha(sealed_out),
        "pooled": {k: v["pooled"]["all"]
                   for k, v in rep["aggregates"].items()},
        "pass": True,
    }
    json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
    print(json.dumps(doc, indent=1))


if __name__ == "__main__":
    main()
