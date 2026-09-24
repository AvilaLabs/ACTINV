#!/usr/bin/env python3
"""P44 G0 opening seal: protocol digest, frozen partition assignment,
scoring-code identities, band definitions, corpus/input identities,
envelope. Bound before any coverage value exists.

Every declared pin is recomputed; a mismatch aborts the seal instead of
recording a false identity.
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g0_p44_seals.json")
DATA = os.path.expanduser("~/nuclear-data")
FNS = os.path.join(DATA, "conderc-fns", "fns")
WORK = os.path.join(DATA, "p44-work")

PROTOCOL = "protocols/ACTINV-P44_PROTOCOL.md"

sys.path.insert(0, os.path.join(ROOT, "controls"))
import p44_bands  # noqa: E402


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def ident(path):
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    return {"path": path, "sha256": sha(p)}


def pinned(path, declared):
    entry = ident(path)
    if entry["sha256"] != declared:
        raise SystemExit(
            f"pin mismatch for {path}: computed {entry['sha256']}, "
            f"declared {declared}")
    return entry


def corpus_manifest():
    """Per-file sha over every corpus input (.i, .exp, *_fluxes)."""
    files = {}
    for m, exp in p44_bands.experiments():
        d = os.path.join(FNS, m)
        for name in (f"TENDL-2017_{exp}.i", f"{exp}.exp",
                     f"{exp}_fluxes"):
            p = os.path.join(d, name)
            files[f"{m}/{name}"] = sha(p)
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(f"{k}:{files[k]}\n".encode())
    return files, h.hexdigest()


def main():
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.strip()
    prior = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "results"))):
        if f.startswith("verdict_") and f.endswith(".json"):
            doc = json.load(open(os.path.join(ROOT, "results", f)))
            prior[f] = doc.get("verdict", doc.get("disposition"))
    all_exps = p44_bands.experiments()
    development = sorted(e for e in all_exps if e in p44_bands.DEVELOPMENT)
    sealed = sorted(e for e in all_exps if e not in p44_bands.DEVELOPMENT)
    files, corpus_sha = corpus_manifest()
    seals = {
        "schema": "actinv-g0-seal-1",
        "phase": "P44",
        "recorded_at_utc":
            subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                           capture_output=True, text=True).stdout.strip(),
        "opening_commit": head,
        "protocol": PROTOCOL,
        "protocol_sha256": sha(os.path.join(ROOT, PROTOCOL)),
        "identities": {
            "actinv_binary": ident("target/release/actinv"),
            "activation_library": pinned(
                "actinv-data/v1.1.0/activation/"
                "tendl-2025-patched-neutron-709g.npz",
                p44_bands.LIBRARY_SHA),
            "covariance_sidecar": pinned(
                os.path.join(DATA, "p43-work", "p43.cov.npz"),
                p44_bands.COVARIANCE_SHA),
            "decay_primary": pinned(
                p44_bands.DECAY_PRIMARY,
                "6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb"),
            "decay_fallback": pinned(
                p44_bands.DECAY_FALLBACK,
                "850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123"),
            "fns_archive": ident(
                os.path.join(DATA, "conderc-fns", "fns.zip")),
        },
        "corpus_manifest_sha256": corpus_sha,
        "corpus_files": files,
        "code_sha256": {
            "scorer": sha(os.path.join(ROOT, "controls",
                                       "p44_band_coverage.py")),
            "driver": sha(os.path.join(ROOT, "controls", "p44_bands.py")),
            "corpus_reader": sha(os.path.join(ROOT, "controls", "harness",
                                              "fispact_io.py")),
        },
        "partitions": {
            "development": [list(e) for e in development],
            "sealed": [list(e) for e in sealed],
        },
        "band_definitions": {
            "confidence_level": p44_bands.LEVEL,
            "tail": p44_bands.TAIL,
            "samples": p44_bands.SAMPLES,
            "seed": p44_bands.SEED,
            "channels": ["cross_section_mf33", "decay_constants"],
            "first_order": {
                "responses": ["heat.total"],
                "band": "normal_interval",
            },
            "sampled": {
                "responses": ["decay_heat_w_per_g"],
                "band": "central quantile interval [Q(tail), Q(1-tail)], "
                        "type-7 linear interpolation",
                "flux_rel_std": 0.0,
                "composition_rel_std": {},
                "fission_yields": False,
                "first_order_comparison": False,
            },
        },
        "scoring_rules": {
            "level": p44_bands.LEVEL,
            "metrics": ["band_only", "combined_sigma"],
            "boundary": "inclusive",
            "denominator": "covered/(covered+not_covered+undefined)",
            "undefined_rows": "no_cooling_step_within_2_percent",
            "excluded_nonpoints": ["nonpositive_time",
                                   "nonpositive_measurement"],
        },
        "envelope": {"sealed_scoring_minutes": 180},
        "prior_verdicts": prior,
        "amendments": [],
    }
    json.dump(seals, open(OUT, "w"), indent=2, sort_keys=True)
    print(f"sealed P44 at {head} -> {OUT}")
    print(f"  development: {len(development)} experiments; "
          f"sealed: {len(sealed)} experiments; "
          f"corpus files: {len(files)}")


if __name__ == "__main__":
    main()
