#!/usr/bin/env python3
"""P24 G3 — diagnostic re-score of the consumed P17 partition.

Re-scores the consumed P17 rows (Tables 23, 25, 36) under the frozen P24
corrected definitions.  Every source row lands exactly once as scored or
carries its frozen predicate; the falsification rows must land on their
corrected named outcomes (Ag109g -> declared LFS=2 partial, bare captures
-> unsupported_self_shielding, EOI rows -> pulse-limit reconstruction).

This is public diagnostic evidence only — no fresh-partition value is
read here.  A green independent checker on this record is the protocol's
sole authorization to unseal the fresh partition at G4.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

from p17_heldout import (
    FIELD_SPECTRUM_MAT,
    checked_inputs as p17_checked_inputs,
    decay_by_identity,
    extracted_inputs as p17_extracted_inputs,
    fold_group_response,
    parse_maxwellian_table,
    parse_si_table,
    pointwise_response_macs,
    production_macs,
    production_spectrum_response,
    selected_endf_catalog,
    spectrum_catalog,
)
from p17_irdff import ProductionLibrary, PRODUCTION_LIBRARY, PRODUCTION_INDEX
from p17_scoring import (
    all_family_metrics,
    score_calculation,
    unscored_calculation,
)
import p24_definitions
import p24_scorer
from p24_scorer import (
    find_monitor_row,
    inclusion_predicate,
    internal_consistency,
    monitor_self_reason,
    experimental_value,
    row_binding,
)

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"
DEFS_RECORD = RESULTS / "g1_p24_definitions.json"
P17_RECORD = RESULTS / "g5_p17_heldout.json"

CANDIDATE_NPZ = REPO / "target" / "p24-g0" / "candidate-neutron.npz"
CANDIDATE_INDEX = REPO / "target" / "p24-g0" / "candidate-neutron_index.json"
CANDIDATE_NPZ_SHA256 = "e569d14ba17305456c4a55295a327e4bc485fa18f04655fc9745f32da59cbfbf"
CANDIDATE_INDEX_SHA256 = "74e68d9164cd2675b24692e7fd3d93726218bcb5a58f1e2a23953161641b6920"
BASELINE_NPZ_SHA256 = "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
BASELINE_INDEX_SHA256 = "8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb"
G0_SEAL = RESULTS / "g0_p24_seals.json"

# Consumed-partition specs: the P24 predicates need the field name for the
# dilute-validity gate.  T36 is the LB44 Maxwellian field — the one field
# whose dilute validity is evidenced on the consumed partition.
CONSUMED_SPECS = {
    23: {"kind": "eoi_si", "field": "SPR-III central cavity",
         "family": "H1_SPR_III_table_23", "spectrum_mats": (FIELD_SPECTRUM_MAT[23],)},
    25: {"kind": "eoi_si", "field": "ACRR central cavity",
         "family": "H2_ACRR_table_25", "spectrum_mats": (FIELD_SPECTRUM_MAT[25],)},
    36: {"kind": "maxwellian_sacs", "field": "LB44 Maxwellian",
         "family": "H3_Maxwellian_table_36", "spectrum_mats": ()},
}

VARIANTS = {
    "official": {
        "input_set_id": "irdff_ii_fold",
        "interpretation": "Independent fold of the hash-pinned IRDFF-II archive "
                          "(groupwise for SI, pointwise for Maxwellian SACS); "
                          "official-data context, not an ACTINV prediction.",
    },
    "candidate": {
        "input_set_id": "candidate_neutron_artifact",
        "interpretation": "P25-repaired bounded candidate build over the IRDFF-II "
                          "target set (G0 artifact).",
    },
    "baseline": {
        "input_set_id": "v101_release_baseline",
        "interpretation": "Signed release v1.0.1 shipped TENDL-2025 709-group "
                          "neutron library; pinned baseline comparator.",
    },
    "published": {
        "input_set_id": "published",
        "interpretation": "Published IRDFF-II calculated column; different-data "
                          "context, never an ACTINV prediction.",
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def p17_mapping(binding: dict) -> dict:
    """Adapt a P24 binding to the P17 fold-machinery mapping shape.

    ``product_lfs`` is the production-library isomer selector: None for an
    MF=3 total row, the decay-archive LISO for an MF=10 partial.
    """
    if binding.get("kind") == "composite_fission_foil":
        return {
            "kind": "composite_fission_foil",
            "source_label": binding["source_label"],
            "components": binding["components"],
            "is_fission": True,
            "decay_product_za": None,
            "decay_product_liso": None,
        }
    if binding.get("channel") == "mf10_partial":
        return {
            "kind": "simple",
            "source_label": binding["source_label"],
            "target_za": int(binding["target_za"]),
            "mt": int(binding["mt"]),
            "product_za": int(binding["product_za"]),
            "product_lfs": binding.get("decay_product_liso"),
            "raw_evaluation_lfs": int(binding["raw_evaluation_lfs"]),
            "decay_product_za": int(binding["product_za"]),
            "decay_product_liso": binding.get("decay_product_liso"),
            "is_fission": bool(binding.get("is_fission")),
        }
    return {
        "kind": "simple",
        "source_label": binding["source_label"],
        "target_za": int(binding["target_za"]),
        "mt": int(binding["mt"]),
        "product_za": None,
        "product_lfs": None,
        "raw_evaluation_lfs": None,
        "decay_product_za": binding.get("decay_product_za"),
        "decay_product_liso": binding.get("decay_product_liso"),
        "is_fission": bool(binding.get("is_fission")),
    }


def score_eoi_row(source, spec, binding, monitor_row, decays,
                  spectra, group_mf3, group_mf10, libraries):
    """Re-score one consumed EOI spectral-index row under P24."""
    mat = spec["spectrum_mats"][0]
    spectrum = spectra[mat]
    reason = monitor_self_reason(source, monitor_row, "eoi_si") \
        or inclusion_predicate(source, spec, binding)
    exp, exp_reason = experimental_value(
        source, spec, binding or {}, monitor_row, decays)
    if reason == "scored" and exp_reason != "scored":
        reason = exp_reason
    if reason == "scored" and not (isinstance(exp, float) and math.isfinite(exp) and exp > 0):
        reason = "nonfinite_experimental_value" if exp is None else "nonpositive_experimental_value"

    mapping = p17_mapping(binding) if binding and not binding.get("unbound") else None
    calcs = {}
    if reason == "scored":
        # official fold: groupwise ratio against the Ni58p monitor
        official_si = None
        official_keys: list = []
        mon_map = None
        if mapping is not None:
            mon_map = p17_mapping(row_binding("Ni58p", ALIAS_INDEX))
            num, kn = fold_group_response(mapping, group_mf3, group_mf10, spectrum)
            den, kd = fold_group_response(mon_map, group_mf3, group_mf10, spectrum)
            official_keys = list(kn) + list(kd)
            if num is not None and den:
                official_si = num / den
        calcs["official"] = score_calculation(
            exp, official_si,
            input_set_id=VARIANTS["official"]["input_set_id"],
            interpretation=VARIANTS["official"]["interpretation"]) if official_si is not None \
            else unscored_calculation(
                input_set_id=VARIANTS["official"]["input_set_id"],
                reason="variant_reaction_unavailable",
                interpretation=VARIANTS["official"]["interpretation"])
        for name, lib in libraries.items():
            val = None
            vreason = "variant_reaction_unavailable"
            if mapping is not None:
                n, rn, evn = production_spectrum_response(lib, mapping, spectrum)
                d, rd, evd = production_spectrum_response(lib, mon_map, spectrum)
                if rn == "scored" and rd == "scored" and n is not None and d:
                    val = n / d
                    vreason = "scored"
                elif rn != "scored":
                    vreason = rn
                else:
                    vreason = rd
            calc = score_calculation(
                exp, val,
                input_set_id=VARIANTS[name]["input_set_id"],
                interpretation=VARIANTS[name]["interpretation"]) if vreason == "scored" \
                else unscored_calculation(
                    input_set_id=VARIANTS[name]["input_set_id"],
                    reason=vreason,
                    interpretation=VARIANTS[name]["interpretation"])
            # record which leg of the ratio failed so a missing monitor
            # target is never attributed to the row's own target
            if vreason != "scored" and mapping is not None:
                missing = []
                if int(mapping["target_za"]) not in lib.target_by_za:
                    missing.append({"leg": "numerator",
                                    "target_za": int(mapping["target_za"])})
                if mon_map is not None and \
                        int(mon_map["target_za"]) not in lib.target_by_za:
                    missing.append({"leg": "monitor",
                                    "target_za": int(mon_map["target_za"])})
                if missing:
                    calc["unavailable_legs"] = missing
            calcs[name] = calc
        # published context: the printed SI is the paper's own observable
        pub = source.get("published_spectral_index")
        calcs["published"] = score_calculation(
            exp, float(pub) if pub else None,
            input_set_id="published",
            interpretation=VARIANTS["published"]["interpretation"])
        ev = {"official_group_keys": official_keys}
    else:
        for name in VARIANTS:
            calcs[name] = unscored_calculation(
                input_set_id=VARIANTS[name]["input_set_id"],
                reason="not_applicable",
                interpretation=VARIANTS[name]["interpretation"])
        ev = {}
    uncertainty = math.hypot(
        float(source.get("experimental_uncertainty_percent") or 0.0),
        float((monitor_row or {}).get("experimental_uncertainty_percent") or 0.0))
    return exp, reason, calcs, ev, uncertainty


def score_maxwellian_row(source, spec, binding, point_mf3, point_mf10,
                         group_mf3, group_mf10, libraries):
    """Re-score one consumed Maxwellian SACS row (Table 36, LB44 field)."""
    stellar = source.get("reference") != "[210]"
    kt = float(source["kT_lab_keV"])
    reason = inclusion_predicate(source, spec, binding)
    if reason == "scored":
        # dilute gate applies to the Maxwellian family too
        if binding and int(binding.get("mt", 0)) in p24_definitions.RESONANCE_STRUCTURED_MT \
                and spec["field"] not in p24_definitions.DILUTE_VERIFIED_FIELDS:
            reason = "unsupported_self_shielding"
    exp = float(source["measured_mb"]) if source.get("measured_mb") else None
    if reason == "scored" and not (exp and math.isfinite(exp) and exp > 0):
        reason = "nonfinite_experimental_value" if exp is None else "nonpositive_experimental_value"

    mapping = p17_mapping(binding) if binding and not binding.get("unbound") else None
    calcs = {}
    ev = {}
    if reason == "scored" and mapping is not None:
        point_v, point_k = pointwise_response_macs(
            mapping, point_mf3, point_mf10, kt, stellar_normalization=stellar)
        group_v, group_k = pointwise_response_macs(
            mapping, group_mf3, group_mf10, kt, stellar_normalization=stellar)
        point_mb = None if point_v is None else 1000.0 * point_v
        group_mb = None if group_v is None else 1000.0 * group_v
        ev = {"official_pointwise_keys": point_k, "official_groupwise_keys": group_k,
              "official_groupwise_mb": group_mb,
              "groupwise_vs_pointwise_relative": (
                  abs(group_mb - point_mb) / point_mb
                  if group_mb is not None and point_mb else None)}
        calcs["official"] = score_calculation(
            exp, point_mb, input_set_id=VARIANTS["official"]["input_set_id"],
            interpretation=VARIANTS["official"]["interpretation"]) if point_mb is not None \
            else unscored_calculation(
                input_set_id=VARIANTS["official"]["input_set_id"],
                reason="variant_reaction_unavailable",
                interpretation=VARIANTS["official"]["interpretation"])
        for name, lib in libraries.items():
            v, vr, vev = production_macs(lib, mapping, kt, stellar_normalization=stellar)
            vmb = None if v is None else 1000.0 * v
            calcs[name] = score_calculation(
                exp, vmb, input_set_id=VARIANTS[name]["input_set_id"],
                interpretation=VARIANTS[name]["interpretation"]) if vmb is not None \
                else unscored_calculation(
                    input_set_id=VARIANTS[name]["input_set_id"],
                    reason=vr, interpretation=VARIANTS[name]["interpretation"])
        # published context: literal printed calculated cell (row 21 is a
        # known source inconsistency — value preserved, never scored)
        calcs["published"] = unscored_calculation(
            input_set_id="published",
            reason="different_data_context",
            interpretation="Literal published IRDFF-II calculated cell.",
            value=float(source["published_calculated_mb"]))
    else:
        for name in VARIANTS:
            calcs[name] = unscored_calculation(
                input_set_id=VARIANTS[name]["input_set_id"],
                reason="not_applicable",
                interpretation=VARIANTS[name]["interpretation"])
    return exp, reason, calcs, ev, float(source.get("experimental_uncertainty_percent") or 0.0)


def main() -> int:
    global ALIAS_INDEX
    failures = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        raise RuntimeError("P24 protocol identity changed")

    defs = json.loads(DEFS_RECORD.read_text())
    module_hash = hashlib.sha256(
        Path(p24_definitions.__file__).read_bytes()).hexdigest()
    if defs["frozen_definition_module"]["sha256"] != module_hash:
        failures.append("frozen definition module hash differs from G1 record")
    scorer_hash = hashlib.sha256(
        Path(p24_scorer.__file__).read_bytes()).hexdigest()
    audit = json.loads((RESULTS / "g2_p24_scorer_audit.json").read_text())
    if audit["freeze"]["scorer_sha256"] != scorer_hash:
        failures.append("scorer hash differs from the G2-audited freeze")
    if not audit.get("pass"):
        failures.append("G2 audit record is not green")

    # artifact identities (G0 seal is the authority)
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

    ALIAS_INDEX = p24_scorer.load_alias_index(defs)

    libraries = {
        "candidate": ProductionLibrary(CANDIDATE_NPZ, CANDIDATE_INDEX),
        "baseline": ProductionLibrary(PRODUCTION_LIBRARY, PRODUCTION_INDEX),
    }

    rows_out = []
    landing = {"ag109g_lfs2": [], "bare_capture_unsupported": [],
               "monitor_rows": [], "pulse_reconstruction_residuals": []}
    with p17_extracted_inputs() as paths:
        decays = decay_by_identity(paths["decay"])
        spectra = spectrum_catalog(paths["spectrum"])

        # --- EOI SI families (Tables 23, 25) ---
        for table in (23, 25):
            spec = CONSUMED_SPECS[table]
            sources = parse_si_table(table)
            monitor_row = find_monitor_row(sources)
            bindings = [row_binding(s["reaction_label"], ALIAS_INDEX)
                        for s in sources]
            mappings = [p17_mapping(b) for b in bindings
                        if not b.get("unbound")]
            # the field monitor's catalog keys must be selected too
            mappings.append(p17_mapping(row_binding("Ni58p", ALIAS_INDEX)))
            group_mf3, group_mf10 = selected_endf_catalog(
                paths["group"], mappings)
            for source, binding in zip(sources, bindings):
                exp, reason, calcs, ev, unc = score_eoi_row(
                    source, spec, binding, monitor_row, decays,
                    spectra, group_mf3, group_mf10, libraries)
                row = p24_scorer.p24_make_row(
                    row_id=f"p24-g3-t{table}-r{source['table_row']:03d}-{source['label']}",
                    family=spec["family"],
                    source_id=f"IRDFF-II:Table-{table}:row-{source['table_row']:03d}",
                    source_record={k: v for k, v in source.items()
                                   if k not in ("mapping", "post_failure_mapping",
                                                "experimental_spectral_index",
                                                "post_failure_pulse_spectral_index",
                                                "production_eligibility")},
                    observable="spectral_index_relative_to_Ni58p",
                    unit="dimensionless",
                    experimental_value=exp,
                    experimental_uncertainty=unc,
                    experimental_uncertainty_unit="percent; RSS of printed EOI activity uncertainties",
                    inclusion_reason=reason,
                    calculations=calcs)
                if ev:
                    row["heldout_evidence"] = ev
                rows_out.append(row)

                # falsification-row landing bookkeeping
                if source["reaction_label"] == "Ag109g" and binding:
                    landing["ag109g_lfs2"].append(
                        binding.get("raw_evaluation_lfs") == 2
                        and binding.get("channel") == "mf10_partial")
                if reason == "unsupported_self_shielding" and \
                        binding and int(binding.get("mt", 0)) == 102:
                    landing["bare_capture_unsupported"].append(row["row_id"])
                if reason == "monitor_identity_not_predictive":
                    landing["monitor_rows"].append(row["row_id"])
                # honest residual: reconstruction vs the publication's
                # *inferred* measured SI (printed SI / printed C/E), the
                # same denominator the G1 demonstration used
                pub_si = source.get("published_spectral_index")
                pub_ce = source.get("published_SI_C_over_E")
                if reason == "scored" and exp and pub_si and pub_ce:
                    inferred = float(pub_si) / float(pub_ce)
                    landing["pulse_reconstruction_residuals"].append(
                        abs(exp - inferred) / inferred)

        # --- Maxwellian SACS family (Table 36, LB44 field) ---
        spec = CONSUMED_SPECS[36]
        h3_sources = parse_maxwellian_table()
        h3_bindings = [row_binding(s["label"], ALIAS_INDEX) for s in h3_sources]
        h3_mappings = [p17_mapping(b) for b in h3_bindings if not b.get("unbound")]
        group_mf3, group_mf10 = selected_endf_catalog(paths["group"], h3_mappings)
        point_mf3, point_mf10 = selected_endf_catalog(paths["pointwise"], h3_mappings)
        for source, binding in zip(h3_sources, h3_bindings):
            exp, reason, calcs, ev, unc = score_maxwellian_row(
                source, spec, binding, point_mf3, point_mf10,
                group_mf3, group_mf10, libraries)
            row = p24_scorer.p24_make_row(
                row_id=f"p24-g3-t36-r{source['table_row']:03d}-{source['label']}",
                family=spec["family"],
                source_id=f"IRDFF-II:Table-36:row-{source['table_row']:03d}",
                source_record=source,
                observable="Maxwellian_spectrum_averaged_cross_section",
                unit="mb",
                experimental_value=exp,
                experimental_uncertainty=unc,
                experimental_uncertainty_unit="percent",
                inclusion_reason=reason,
                calculations=calcs)
            if ev:
                row["heldout_evidence"] = ev
            rows_out.append(row)
            if source["label"] == "Ag109g" and binding:
                landing["ag109g_lfs2"].append(
                    binding.get("raw_evaluation_lfs") == 2
                    and binding.get("channel") == "mf10_partial")
            if reason == "unsupported_self_shielding" and \
                    binding and int(binding.get("mt", 0)) == 102:
                landing["bare_capture_unsupported"].append(row["row_id"])

    reason_counts = {}
    for r in rows_out:
        reason_counts[r["inclusion"]["reason"]] = \
            reason_counts.get(r["inclusion"]["reason"], 0) + 1

    # P17 had 94 consumed rows; the re-score must account for every one
    p17 = json.loads(P17_RECORD.read_text())
    p17_ids = {r["source_id"] for r in p17["rows"]}
    p24_ids = {r["source_id"] for r in rows_out}
    completeness = {
        "p17_source_ids": len(p17_ids),
        "p24_source_ids": len(p24_ids),
        "missing": sorted(p17_ids - p24_ids),
        "extra": sorted(p24_ids - p17_ids),
        "complete": p17_ids == p24_ids,
    }

    landing_checks = {
        "ag109g_binds_declared_lfs2_partial": bool(landing["ag109g_lfs2"])
        and all(landing["ag109g_lfs2"]),
        "all_bare_captures_unsupported": len(landing["bare_capture_unsupported"]) > 0,
        "monitor_rows_ledgered_not_scored": len(landing["monitor_rows"]) == 2,
        "max_pulse_residual": (max(landing["pulse_reconstruction_residuals"])
                               if landing["pulse_reconstruction_residuals"] else None),
    }

    record = {
        "schema": "actinv-p24-g3-diagnostic-1",
        "phase": "P24",
        "gate": "G3",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_sha256_verified": True,
        "module_hashes": {
            "controls/p24_definitions.py": module_hash,
            "controls/p24_scorer.py": scorer_hash,
        },
        "artifact_identities": {
            "candidate_npz": CANDIDATE_NPZ_SHA256,
            "candidate_index": CANDIDATE_INDEX_SHA256,
            "baseline_npz": BASELINE_NPZ_SHA256,
            "baseline_index": BASELINE_INDEX_SHA256,
        },
        "scope": "consumed P17 partition re-scored under the frozen P24 "
                 "definitions; no fresh-partition value was read",
        "irdff_input_identities": p17_checked_inputs(),
        "row_counts": {"total": len(rows_out), "by_reason": reason_counts},
        "completeness_vs_p17": completeness,
        "falsification_row_landing": landing_checks,
        "family_metrics": all_family_metrics(rows_out),
        "rows": rows_out,
        "pass": not failures and completeness["complete"]
        and landing_checks["ag109g_binds_declared_lfs2_partial"]
        and landing_checks["monitor_rows_ledgered_not_scored"],
        "prefixed_failures": failures,
    }
    out = RESULTS / "g3_p24_diagnostic.json"
    out.write_text(json.dumps(record, indent=1, sort_keys=True,
                              default=str) + "\n")
    print(json.dumps({k: record[k] for k in
                      ("pass", "row_counts", "falsification_row_landing",
                       "completeness_vs_p17", "prefixed_failures")},
                     indent=1, sort_keys=True, default=str))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
