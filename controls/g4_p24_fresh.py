#!/usr/bin/env python3
"""P24 G4 — one-time fresh-partition read and scoring.

Unseals the fresh partition (Tables 26-35, 41-46).  Each table is parsed
exactly once through the frozen scorer (``p24_scorer.parse_fresh_table``);
the parsed rows drive both the official-catalog selection and the row
scoring, so no table is ever parsed twice.  Every source row lands exactly
once as scored or ledgered with a frozen predicate, on identical rows for
the candidate, baseline and official variants.  Writes
``results/g4_p24_fresh.json``.

No post-read definition, mapping, metric or exclusion change is permitted;
this record preserves every source line and every reason verbatim so the
G5 closure checker can re-derive the outcome independently.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from endf_common import interp_eval, read_tab1, sections
from p17_heldout import (
    catalog_record,
    checked_inputs as p17_checked_inputs,
    decay_by_identity,
    extracted_inputs as p17_extracted_inputs,
    fold_group_response,
    production_spectrum_response,
    selected_endf_catalog,
    _production_row,
)
from p17_irdff import ProductionLibrary, PRODUCTION_LIBRARY, PRODUCTION_INDEX
from p17_scoring import unscored_calculation
import p24_definitions
import p24_scorer
from p24_scorer import (
    FIELD_MAT,
    I0_HIGH_EV,
    I0_LOW_EV,
    SIGMA0_EV,
    TABLE_SPECS,
    find_monitor_row,
    load_alias_index,
    parse_fresh_table,
    row_binding,
)
from g3_p24_diagnostic import (
    BASELINE_INDEX_SHA256,
    BASELINE_NPZ_SHA256,
    CANDIDATE_INDEX,
    CANDIDATE_INDEX_SHA256,
    CANDIDATE_NPZ,
    CANDIDATE_NPZ_SHA256,
    VARIANTS,
    p17_mapping,
)

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUTPUT = RESULTS / "g4_p24_fresh.json"
LEDGER = RESULTS / "g4_p24_row_ledger.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
DEFS_RECORD = RESULTS / "g1_p24_definitions.json"
G0_SEAL = RESULTS / "g0_p24_seals.json"
G1_CHECK = RESULTS / "g1_p24_check.json"
G2_AUDIT = RESULTS / "g2_p24_scorer_audit.json"
G2_CHECK = RESULTS / "g2_p24_check.json"
G3_CHECK = RESULTS / "g3_p24_check.json"

FRESH_MATS = {mat for spec in TABLE_SPECS.values() for mat in spec["spectrum_mats"]}

_GAUSS_NODES, _GAUSS_WEIGHTS = np.polynomial.legendre.leggauss(16)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fresh_spectra(path: Path) -> dict[int, tuple]:
    """MAT -> TAB1 for every fresh-partition spectrum (histogram law only)."""
    out = {}
    for (mat, mf, mt), lines in sections(path):
        if mat in FRESH_MATS and mf == 3 and mt == 261:
            record, _ = read_tab1(lines, 1)
            if any(law != 1 for _, law in record[6]):
                raise RuntimeError(f"spectrum MAT {mat} is not histogram law")
            out[mat] = record
    missing = FRESH_MATS - set(out)
    if missing:
        raise RuntimeError(f"missing fresh spectra: {sorted(missing)}")
    return out


def pointwise_i0(record: tuple) -> float:
    """Kayzero-convention resonance integral: int sigma(E)/E dE over
    [0.55 eV, 2 MeV], honoring the record's interpolation regions via
    per-interval Gauss-Legendre quadrature."""
    _, _, _, _, _, _, nbt, xs, ys = record
    energy = np.asarray(xs, dtype=float)
    sigma = np.asarray(ys, dtype=float)
    lower = np.maximum(energy[:-1], I0_LOW_EV)
    upper = np.minimum(energy[1:], I0_HIGH_EV)
    mask = upper > lower
    lower, upper = lower[mask], upper[mask]
    total = 0.0
    for start in range(0, len(lower), 20_000):
        stop = min(start + 20_000, len(lower))
        lo, hi = lower[start:stop], upper[start:stop]
        midpoint = 0.5 * (lo + hi)
        half_width = 0.5 * (hi - lo)
        nodes = midpoint[:, None] + half_width[:, None] * _GAUSS_NODES[None, :]
        values = interp_eval(energy, sigma, nbt, nodes.ravel()).reshape(nodes.shape)
        total += float(np.sum(
            half_width[:, None] * _GAUSS_WEIGHTS[None, :] * values / nodes))
    return total


def main() -> int:
    failures = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise RuntimeError("P24 protocol identity changed")

    defs = json.loads(DEFS_RECORD.read_text())
    if hashlib.sha256(Path(p24_definitions.__file__).read_bytes()).hexdigest() \
            != defs["frozen_definition_module"]["sha256"]:
        failures.append("frozen definition module hash differs from G1 record")
    scorer_hash = hashlib.sha256(Path(p24_scorer.__file__).read_bytes()).hexdigest()
    audit = json.loads(G2_AUDIT.read_text())
    if audit["freeze"]["scorer_sha256"] != scorer_hash:
        failures.append("scorer hash differs from the G2-audited freeze")
    for name, path in (("G2 audit", G2_AUDIT), ("G1 check", G1_CHECK),
                       ("G2 check", G2_CHECK), ("G3 check", G3_CHECK)):
        if not json.loads(path.read_text()).get("pass"):
            failures.append(f"{name} record is not green")

    seal = json.loads(G0_SEAL.read_text())["artifacts"]
    if sha256(CANDIDATE_NPZ) != CANDIDATE_NPZ_SHA256 or \
            seal["candidate_npz_sha256"] != CANDIDATE_NPZ_SHA256:
        failures.append("candidate NPZ identity mismatch")
    if sha256(CANDIDATE_INDEX) != CANDIDATE_INDEX_SHA256:
        failures.append("candidate index identity mismatch")
    if sha256(PRODUCTION_LIBRARY) != BASELINE_NPZ_SHA256 or \
            sha256(PRODUCTION_INDEX) != BASELINE_INDEX_SHA256:
        failures.append("v1.0.1 baseline artifact identity mismatch")
    if failures:
        print(json.dumps({"pass": False, "failures": failures}, indent=1))
        return 1

    identities = p17_checked_inputs()
    alias_index = load_alias_index(defs)

    # ---- the one-time read: every table parsed exactly once ----
    parsed_tables = {t: parse_fresh_table(t) for t in sorted(TABLE_SPECS)}

    # collect every binding the official/production folds may need:
    # row bindings plus every monitor label appearing in the partition
    monitor_labels = {p24_scorer.MONITOR_LABEL}
    bindings = {}
    for table, parsed in parsed_tables.items():
        for row in parsed:
            if row.get("monitor_label"):
                monitor_labels.add(row["monitor_label"])
            reaction = row.get("reaction_label") or row.get("label", "")
            try:
                binding = row_binding(reaction, alias_index)
            except ValueError:
                binding = None
            if binding is not None and not binding.get("unbound"):
                bindings[reaction] = binding
    monitor_bindings = {}
    for label in sorted(monitor_labels):
        try:
            binding = row_binding(label, alias_index)
        except ValueError:
            binding = None
        if binding is not None and not binding.get("unbound"):
            monitor_bindings[label] = binding
    all_mappings = [p17_mapping(b) for b in bindings.values()]
    all_mappings += [p17_mapping(b) for b in monitor_bindings.values()]

    with p17_extracted_inputs() as paths:
        decays = decay_by_identity(paths["decay"])
        spectra = fresh_spectra(paths["spectrum"])
        group_mf3, group_mf10 = selected_endf_catalog(paths["group"], all_mappings)
        point_mf3, point_mf10 = selected_endf_catalog(paths["pointwise"], all_mappings)

        def fold_response(binding, spectrum):
            return fold_group_response(
                p17_mapping(binding), group_mf3, group_mf10, spectrum)

        def production_response(lib, binding, spectrum):
            value, reason, evidence = production_spectrum_response(
                lib, p17_mapping(binding), spectrum)
            return value, evidence, reason

        def thermal_response(binding, kind):
            mapping = p17_mapping(binding)
            if mapping["kind"] != "simple":
                return None, [], "variant_reaction_unavailable"
            record = catalog_record(mapping, point_mf3, point_mf10)
            if record is None:
                return None, [], "variant_reaction_unavailable"
            if mapping["product_lfs"] is None:
                key = [int(mapping["target_za"]), 3, int(mapping["mt"])]
            else:
                key = [int(mapping["target_za"]), 10, int(mapping["mt"]),
                       int(mapping["product_za"]), int(mapping["raw_evaluation_lfs"])]
            if kind == "sigma0":
                value = float(interp_eval(
                    np.asarray(record[7], float), np.asarray(record[8], float),
                    record[6], np.array([SIGMA0_EV]))[0])
            else:
                value = pointwise_i0(record)
            return value, [key], "scored"

        def production_thermal(lib, binding, kind):
            mapping = p17_mapping(binding)
            if mapping["kind"] != "simple":
                return None, [], "variant_reaction_unavailable"
            sigma, evidence = _production_row(lib, mapping)
            if sigma is None:
                return None, [], str(evidence["reason"])
            if kind == "sigma0":
                index = int(np.clip(
                    np.searchsorted(lib.bounds, SIGMA0_EV, side="right") - 1,
                    0, len(lib.bounds) - 2))
                value = float(sigma[index])
            else:
                lo = np.maximum(lib.bounds[:-1], I0_LOW_EV)
                hi = np.minimum(lib.bounds[1:], I0_HIGH_EV)
                weights = np.where(hi > lo, np.log(hi / lo), 0.0)
                value = float(np.dot(sigma, weights))
            return value, [evidence], "scored"

        context = {
            "alias_index": alias_index,
            "decay_by_id": decays,
            "spectra": spectra,
            "fold_response": fold_response,
            "production_response": production_response,
            "thermal_response": thermal_response,
            "production_thermal": production_thermal,
            "monitor_bindings": monitor_bindings,
            # "published" is not a fold variant: the printed calculated
            # column is attached per-row below, never scored
            "libraries": {
                "official": None,
                "candidate": ProductionLibrary(CANDIDATE_NPZ, CANDIDATE_INDEX),
                "baseline": ProductionLibrary(PRODUCTION_LIBRARY, PRODUCTION_INDEX),
            },
            "input_set_ids": {k: v["input_set_id"] for k, v in VARIANTS.items()},
            "interpretations": {k: v["interpretation"] for k, v in VARIANTS.items()},
        }
        result = p24_scorer.score_fresh_partition(
            defs, context=context, parsed_tables=parsed_tables)

    # the published column is context, not a fold — attach the printed
    # calculated cell (or printed SI for EOI rows) per row verbatim
    for row in result["rows"]:
        src = row["source_record"]
        pub = src.get("published_calculated")
        if pub is None:
            pub = src.get("published_calculated_si")
        if pub is None:
            pub = src.get("published_spectral_index")
        row["calculations"]["published"] = unscored_calculation(
            input_set_id="published",
            reason="different_data_context",
            interpretation=VARIANTS["published"]["interpretation"],
            value=float(pub) if pub is not None else None)

    record = {
        "schema": "actinv-p24-g4-fresh-1",
        "phase": "P24",
        "gate": "G4",
        "protocol_sha256": PROTOCOL_SHA256,
        "input_identities": identities,
        "scorer_sha256": scorer_hash,
        "definitions_sha256": defs["frozen_definition_module"]["sha256"],
        "candidate": {"npz_sha256": CANDIDATE_NPZ_SHA256,
                      "index_sha256": CANDIDATE_INDEX_SHA256},
        "baseline": {"npz_sha256": BASELINE_NPZ_SHA256,
                     "index_sha256": BASELINE_INDEX_SHA256},
        "variants": VARIANTS,
        "pre_unseal_partial_exposure": (
            "During G4 grammar verification, header extraction on sealed "
            "pages incidentally displayed a small number of numeric cells "
            "from T27/T35/T45.  No frozen artifact (protocol, definitions, "
            "scorer) was modified after that exposure — the hashes pinned "
            "at G0/G1/G2 are unchanged and no definition, mapping, metric "
            "or exclusion was fitted to any seen value.  Disclosed for "
            "the audit trail; the formal read is this record."),
        "fresh_tables": {str(t): {"kind": TABLE_SPECS[t]["kind"],
                                  "pages": TABLE_SPECS[t]["pages"],
                                  "rows": len(parsed_tables[t]),
                                  "sha256_of_source_lines": hashlib.sha256(
                                      ("\n".join(r["source_line"] for r in parsed_tables[t]))
                                      .encode()).hexdigest()}
                         for t in sorted(TABLE_SPECS)},
        "monitor_labels_seen": sorted(monitor_labels),
        "bound_reaction_labels": sorted(bindings),
        "row_count": len(result["rows"]),
        "inclusion_histogram": _histogram(result["rows"]),
        "family_metrics": result["family_metrics"],
        "pass": not failures,
    }
    OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    LEDGER.write_text(json.dumps({"rows": result["rows"]}, indent=1, sort_keys=True) + "\n")

    scored = sum(1 for r in result["rows"] if r["inclusion"]["status"] == "scored")
    print(json.dumps({
        "written": str(OUTPUT), "ledger": str(LEDGER),
        "rows": len(result["rows"]), "scored": scored,
        "inclusion_histogram": record["inclusion_histogram"],
        "pass": record["pass"],
    }, indent=1))
    return 0 if record["pass"] else 1


def _histogram(rows: list[dict]) -> dict:
    out = {}
    for row in rows:
        key = row["inclusion"]["reason"]
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


if __name__ == "__main__":
    sys.exit(main())
