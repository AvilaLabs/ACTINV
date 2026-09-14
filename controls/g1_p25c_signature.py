#!/usr/bin/env python3
"""P25c G1: confirmed-leak signature census over the sealed population.

For each of the 81 sealed ``conservation_excess_untraced`` neutron files,
parse the evaluation with the frozen P18b exact-decimal oracle and test the
protocol's signature: an MF=9/MF=10 state record whose leading cross-section
ordinates are exactly equal (as parsed decimals) to the file's own
MF=3/MT=103 first-point value — the upstream-confirmed TALYS
``channelsout.f90`` leak of the thermal (n,p) cross section.

Per matching record the census records (mf, mt, zap, lfs, contaminated leading
ordinate count, reference value).  Records in MT=103 itself are tagged
``self_channel`` and enumerated separately: a first point equal to the (n,p)
total inside the (n,p) record is not the confirmed cross-channel leak.

Files with no signature record are classified ``conservation_excess_
non_signature``; files the oracle cannot parse are ``oracle_error``.

No patch code runs in this gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))

from p18b_decimal_corpus_oracle import parse_evaluation  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def leading_run(ys: tuple, ref: Decimal) -> int:
    """Count leading ordinates whose parsed value equals ref exactly."""
    n = 0
    for v in ys:
        if v.value == ref:
            n += 1
        else:
            break
    return n


def census_file(name: str, det: dict) -> dict:
    path = SOURCE_ROOT / name
    entry = {
        "source_sha256": sha256(path),
        "census_mt": det.get("census_mt"),
        "census_lmf": det.get("census_lmf"),
        "relative_excess": det.get("relative_excess"),
    }
    try:
        ev = parse_evaluation(path, mat_from_file(path))
    except Exception as exc:
        entry["class"] = "oracle_error"
        entry["oracle_error"] = str(exc)[:400]
        return entry
    ref_table = ev.mf3.get(103)
    if ref_table is None or not ref_table.y or ref_table.y[0].value <= 0:
        entry["class"] = "conservation_excess_non_signature"
        entry["reason"] = "no positive MF=3/MT=103 first-point reference"
        return entry
    ref = ref_table.y[0].value
    hits, self_channel = [], []
    for mf, products in ((9, ev.mf9), (10, ev.mf10)):
        for mt, prods in sorted(products.items()):
            for prod in prods:
                run = leading_run(prod.table.y, ref)
                if run:
                    rec = {
                        "mf": mf, "mt": mt, "zap": prod.zap, "lfs": prod.lfs,
                        "contaminated_leading_ordinates": run,
                        "ordinate_count": len(prod.table.y),
                        "first_energy_ev": str(prod.table.x[0]),
                        "leaked_value_barn": str(ref),
                    }
                    (self_channel if mt == 103 else hits).append(rec)
    entry["reference_mt103_first_xs_barn"] = str(ref)
    entry["signature_records"] = hits
    entry["self_channel_records"] = self_channel
    if hits:
        entry["class"] = "confirmed_leak_signature"
    elif self_channel:
        entry["class"] = "self_channel_only"
    else:
        entry["class"] = "conservation_excess_non_signature"
    return entry


def mat_from_file(path: Path) -> int:
    for line in path.read_text("ascii", "replace").splitlines()[:12]:
        try:
            if int(line[70:72]) == 1 and int(line[72:75]) == 451:
                return int(line[66:70])
        except (ValueError, IndexError):
            pass
    raise ValueError(f"{path.name}: no MF=1/MT=451 header")


def main() -> int:
    t0 = time.time()
    seals = json.loads(SEALS.read_text())
    population = seals["sealed_population"]["files"]
    results = {}
    counts = {}
    for name, det in sorted(population.items()):
        rec = census_file(name, det)
        results[name] = rec
        counts[rec["class"]] = counts.get(rec["class"], 0) + 1

    n_sig = sum(
        len(r.get("signature_records", ())) for r in results.values()
    )
    n_ords = sum(
        r["contaminated_leading_ordinates"]
        for r in results.values() for r in r.get("signature_records", ())
    )
    record = {
        "schema": "actinv-p25c-g1-signature-1",
        "gate": "P25c-G1",
        "seals_sha256": sha256(SEALS),
        "population_size": len(population),
        "class_counts": counts,
        "signature_record_count": n_sig,
        "contaminated_ordinate_count": n_ords,
        "files": results,
        "elapsed_s": round(time.time() - t0, 3),
    }
    out = RESULTS / "g1_p25c_signature.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: record[k] for k in
                      ("class_counts", "signature_record_count",
                       "contaminated_ordinate_count", "elapsed_s")}, indent=1))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
