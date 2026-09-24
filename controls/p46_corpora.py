#!/usr/bin/env python3
"""P46 frozen corpus registry + per-corpus experiment driver.

Runs the identical spec->solve->extract pipeline for every admitted
corpus on the FNS partition. Reuses the P44 spec machinery
(`p44_bands.spec_for` shape) with the library block swapped per
corpus and the uncertainty block removed — C/E is a nominal-solve
quantity, and decay data is held fixed across corpora so the
comparison isolates the activation cross-section evaluation.

Produces per-(corpus, experiment) result records carrying the
computed per-step decay heat; scoring lives in p46_score.py.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p44_bands  # noqa: E402  frozen: experiments(), read_flux, DEVELOPMENT
from harness import fispact_io as fio  # noqa: E402

DATA = Path(os.environ.get("ACTINV_DATA", Path.home() / "nuclear-data"))
WORK = Path(os.environ.get("ACTINV_P46_WORK", DATA / "p46-work"))
ACTINV = ROOT / "target/release/actinv"

DECAY_PRIMARY = Path(
    os.environ.get("ACTINV_ENDF_DECAY",
                   DATA / "endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat"))
DECAY_FALLBACK = Path(
    os.environ.get("ACTINV_JEFF_DECAY",
                   DATA / "jeff-3.3-decay/bulk/jeff-3-3_decay.dat"))

CORPORA = {
    "tendl-2025": {
        "path": DATA / "tendl-2025/builds/full/neutron.n.p10.npz",
        "provenance": "legacy_builder",
        "expressibility": ["full_evaluation"],
    },
    "tendl-2025-patched": {
        "path": ROOT / "actinv-data/v1.1.0/activation/"
                        "tendl-2025-patched-neutron-709g.npz",
        "provenance": "current_builder",
        "expressibility": ["covariance_subset"],
    },
    "tendl-2017": {
        "path": DATA / "tendl-2017/build/neutron.n.p10.npz",
        "provenance": "current_builder",
        "expressibility": ["legacy_evaluation"],
    },
    "eaf-2010": {
        "path": DATA / "eaf-2010/actinv_eaf2010_709g.npz",
        "provenance": "legacy_builder",
        "expressibility": ["no_state_catalog"],
    },
    "fendl-3.2c": {
        "path": DATA / "p26b-work/g1-run/actinv_fendl32c_709.npz",
        "provenance": "converted_subset",
        "expressibility": ["converted_subset"],
    },
}

DEVELOPMENT = p44_bands.DEVELOPMENT


def sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def corpus_meta(name: str) -> dict:
    c = CORPORA[name]
    return {"name": name, "path": str(c["path"]),
            "sha256": corpus_sha(name),
            "provenance": c["provenance"],
            "expressibility": c["expressibility"]}


_CORPUS_SHA: dict[str, str] = {}


def corpus_sha(corpus: str) -> str:
    if corpus not in _CORPUS_SHA:
        _CORPUS_SHA[corpus] = sha256(CORPORA[corpus]["path"])
    return _CORPUS_SHA[corpus]


def spec_for(material: str, experiment: str, corpus: str) -> dict:
    """Nominal-solve spec: P44 shape, per-corpus library, no
    uncertainty block, decay data fixed."""
    spec, _inp = p44_bands.spec_for(material, experiment)
    spec["title"] = f"P46 FNS {material} {experiment} {corpus}"
    spec["library"] = {"path": str(CORPORA[corpus]["path"]),
                       "sha256": corpus_sha(corpus)}
    spec.pop("uncertainty", None)
    return spec


def computed_heat(outv: dict) -> list[dict]:
    """per-cooling-step {t_s, heat_w_per_g} from an actinv out.json."""
    steps = outv.get("steps") or []
    irr_end = 0.0
    for st in steps:
        if (st.get("flux") or 0.0) > 0.0:
            irr_end = st.get("t_s", 0.0)
    out = []
    for st in steps:
        t = st.get("t_s", 0.0) - irr_end
        if t <= 0.0:
            continue
        out.append({"t_s": t,
                    "heat_w_per_g": (st.get("heat_W_per_g") or {})
                        .get("total")})
    return out


def run_cli(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True,
                       timeout=3600)
    return p.returncode, (p.stderr or "") + (p.stdout or "")


def produce(corpus: str, material: str, experiment: str) -> dict:
    """One (corpus, experiment) result record; resume-aware."""
    edir = WORK / "runs" / corpus / f"{material}__{experiment}"
    edir.mkdir(parents=True, exist_ok=True)
    outp = edir / "out.json"
    rec = {"corpus": corpus, "material": material,
           "experiment": experiment}

    if outp.is_file():
        try:
            ov = json.loads(outp.read_text())
            rec.update(status="executed", heat=computed_heat(ov),
                       out_sha256=sha256(outp))
            return rec
        except Exception:
            pass  # torn output -> re-execute

    spec = spec_for(material, experiment, corpus)
    spec_path = edir / "spec.json"
    spec_path.write_text(json.dumps(spec))
    rec["spec_sha256"] = sha256(spec_path)

    t0 = time.monotonic()
    rc, log = run_cli([str(ACTINV), "run", str(spec_path), str(outp)])
    wall = time.monotonic() - t0
    rec["wall_s"] = wall
    if rc != 0 or not outp.is_file():
        rec.update(status="failed",
                   error=(log.strip().splitlines()[-1][:300]
                          if log.strip() else f"exit {rc}"))
        return rec
    ov = json.loads(outp.read_text())
    rec.update(status="executed", heat=computed_heat(ov),
               out_sha256=sha256(outp))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=list(CORPORA))
    ap.add_argument("--partition",
                    choices=["development", "sealed", "all"],
                    default="all")
    ap.add_argument("--only", nargs="*", default=None,
                    help="material__experiment subsets (smoke)")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "results/p46_run_ledger.jsonl")
    args = ap.parse_args()

    all_exps = p44_bands.experiments()
    if args.partition == "development":
        todo = [e for e in all_exps if e in DEVELOPMENT]
    elif args.partition == "sealed":
        todo = [e for e in all_exps if e not in DEVELOPMENT]
    else:
        todo = all_exps
    if args.only:
        keep = set(args.only)
        todo = [e for e in todo if f"{e[0]}__{e[1]}" in keep]

    corpora = [args.corpus] if args.corpus else list(CORPORA)
    ledger = args.out
    ledger.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with ledger.open("a") as fh:
        for corpus in corpora:
            for material, experiment in todo:
                rec = produce(corpus, material, experiment)
                rec["produced_at_utc"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                n += 1
                if n % 20 == 0:
                    print(json.dumps({"done": n, "last":
                                      f"{corpus}/{material}/"
                                      f"{experiment}"}),
                          flush=True)
    print(json.dumps({"records": n, "ledger": str(ledger)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
