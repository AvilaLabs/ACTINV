#!/usr/bin/env python3
"""P25c G1 independent checker.

Re-parses every sealed-population file with the frozen P18b
exact-decimal oracle, independently recomputes the MF=3/MT=103 reference
value and every signature/self-channel/residual classification and
contaminated-ordinate count, verifies the record binds to the G0 seal,
and rejects planted mutations.

Imports the frozen oracle (the audited instrument) but no gate module.
Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
sys.path.insert(0, str(REPO / "controls"))
from p18b_decimal_corpus_oracle import parse_evaluation  # noqa: E402

SEALS = RESULTS / "g0_p25c_seals.json"
RECORD = RESULTS / "g1_p25c_signature.json"
SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mat_from_file(path: Path) -> int:
    for line in path.read_text("ascii", "replace").splitlines()[:12]:
        try:
            if int(line[70:72]) == 1 and int(line[72:75]) == 451:
                return int(line[66:70])
        except (ValueError, IndexError):
            pass
    raise ValueError(f"{path.name}: no MF=1/MT=451 header")


def live_classify(name: str) -> dict:
    path = SOURCE_ROOT / name
    out = {"source_sha256": sha256(path)}
    try:
        ev = parse_evaluation(path, mat_from_file(path))
    except Exception:
        out["class"] = "oracle_error"
        return out
    ref_table = ev.mf3.get(103)
    if ref_table is None or not ref_table.y or ref_table.y[0].value <= 0:
        out["class"] = "conservation_excess_non_signature"
        return out
    ref = ref_table.y[0].value
    hits, selfc = [], []
    for mf, products in ((9, ev.mf9), (10, ev.mf10)):
        for mt, prods in sorted(products.items()):
            for prod in prods:
                run = 0
                for v in prod.table.y:
                    if v.value == ref:
                        run += 1
                    else:
                        break
                if run:
                    rec = {
                        "mf": mf, "mt": mt, "zap": prod.zap, "lfs": prod.lfs,
                        "contaminated_leading_ordinates": run,
                        "ordinate_count": len(prod.table.y),
                        "first_energy_ev": str(prod.table.x[0]),
                        "leaked_value_barn": str(ref),
                    }
                    (selfc if mt == 103 else hits).append(rec)
    out["reference_mt103_first_xs_barn"] = str(ref)
    out["signature_records"] = hits
    out["self_channel_records"] = selfc
    out["class"] = ("confirmed_leak_signature" if hits else
                    "self_channel_only" if selfc else
                    "conservation_excess_non_signature")
    return out


def check_report(record: dict) -> list[str]:
    local = []
    if record.get("schema") != "actinv-p25c-g1-signature-1":
        local.append("schema")
    if record.get("seals_sha256") != sha256(SEALS):
        local.append("seals hash")
    if record.get("population_size") != 81:
        local.append("population size")
    files = record.get("files", {})
    if len(files) != 81:
        local.append("files count")
    counts = {}
    for name, rec in files.items():
        counts[rec.get("class")] = counts.get(rec.get("class"), 0) + 1
    if counts != record.get("class_counts"):
        local.append(f"class_counts {record.get('class_counts')} != {counts}")
    n_sig = sum(len(r.get("signature_records", ())) for r in files.values())
    if n_sig != record.get("signature_record_count"):
        local.append("signature_record_count")
    n_ord = sum(
        s["contaminated_leading_ordinates"]
        for r in files.values() for s in r.get("signature_records", ())
    )
    if n_ord != record.get("contaminated_ordinate_count"):
        local.append("contaminated_ordinate_count")
    return local


def main() -> int:
    failures = []
    seals = json.loads(SEALS.read_text())
    record = json.loads(RECORD.read_text())

    population = seals["sealed_population"]["files"]
    for name, det in sorted(population.items()):
        live = live_classify(name)
        rec = record["files"].get(name)
        if rec is None:
            failures.append(f"{name}: missing from census")
            continue
        if live.get("class") != rec.get("class"):
            failures.append(
                f"{name}: class {rec.get('class')} != live {live.get('class')}")
            continue
        if live.get("source_sha256") != det.get("source_sha256"):
            failures.append(f"{name}: source hash drifted")
        for field in ("reference_mt103_first_xs_barn",
                      "signature_records", "self_channel_records"):
            if live.get(field) != rec.get(field):
                failures.append(f"{name}: {field} mismatch")

    failures.extend(check_report(record))

    mutations = rejected = 0
    plants = [
        lambda r: r["files"]["n-Ag104m.tendl"].update(
            {"class": "conservation_excess_non_signature"}),
        lambda r: r["files"]["n-Ag104m.tendl"]["signature_records"]
                  .append({"mf": 10, "mt": 17, "zap": 1, "lfs": 0,
                           "contaminated_leading_ordinates": 1,
                           "ordinate_count": 1, "first_energy_ev": "1",
                           "leaked_value_barn": "1"}),
        lambda r: r.update({"seals_sha256": "0" * 64}),
        lambda r: r.update({"class_counts": {"confirmed_leak_signature": 1}}),
        lambda r: r.update({"contaminated_ordinate_count": 1}),
        lambda r: r["files"].popitem(),
    ]
    for plant in plants:
        m = copy.deepcopy(record)
        plant(m)
        mutations += 1
        if check_report(m):
            rejected += 1
        else:
            print("MUTATION NOT REJECTED")
    if rejected != mutations:
        failures.append(f"mutation self-test: {rejected}/{mutations} rejected")

    result = {
        "schema": "actinv-p25c-g1-check-1",
        "pass": not failures,
        "failures": failures,
        "files_live_reclassified": len(population),
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g1_p25c_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
