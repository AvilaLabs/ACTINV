#!/usr/bin/env python3
"""P26 G2: freeze the comparison contract for the selected workloads.

Emits `results/g2_p26_contract.json`. After this gate the contract is frozen:
any later change is an append-only amendment and makes an otherwise passing
closure conditional. The contract binds the G1 workload record by SHA-256.

Population anchors are hash-pinned where they reference files:
- `fns_709` spectrum is embedded in the committed `examples/fns_fe_5min.json`;
- `irdff_sp_mat9861` is MAT 9861 inside the sealed IRDFF-II spectrum archive.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
G1_RECORD = ROOT / "results" / "g1_p26_workloads.json"
OUT = ROOT / "results" / "g2_p26_contract.json"
IRDFF_SP = Path.home() / "nuclear-data" / "p17-irdff" / "IRDFF-II_sp_g.zip"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

COOLING_TIMES_S = [0.0, 86400.0, 2592000.0, 31536000.0, 315360000.0]
RESPONSES = ["total_activity_bq", "decay_heat_w", "photon_source_per_group", "top5_nuclides_by_activity"]

MATERIALS = {
    "fe": {"basis": "wt_percent", "composition": {"FE": 100.0}},
    "fe_co100wppm": {"basis": "wt_fraction", "composition": {"FE": 0.9999, "CO": 1.0e-4}},
    "fe_nb100wppm": {"basis": "wt_fraction", "composition": {"FE": 0.9999, "NB": 1.0e-4}},
    "w": {"basis": "wt_percent", "composition": {"W": 100.0}},
}

SCHEDULES = {
    "pulse_5min": {"irradiations_s": [300.0], "note": "FNS recorded 5-minute campaign pulse"},
    "cont_2y": {"irradiations_s": [63072000.0], "note": "continuous two-year irradiation"},
}

CAMPAIGN_IMPURITIES = ["CO", "NB", "NI", "MO", "AG", "TA", "V", "CU", "CR", "MN"]
CAMPAIGN_CONCENTRATIONS_WPPM = [1, 10, 100, 1000, 10000, 100000, 300, 3000, 30000, 30]
CAMPAIGN_DURATIONS_S = [60.0, 600.0, 3600.0, 86400.0, 2592000.0]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    g1 = json.loads(G1_RECORD.read_text())
    assert g1["flagship"] == "W-MATCMP"
    fns_example = ROOT / "examples" / "fns_fe_5min.json"

    spectra = {
        "fns_709": {
            "source": {"file": "examples/fns_fe_5min.json", "sha256": sha256_file(fns_example),
                       "json_path": "/spec/spectrum/flux_per_group"},
            "groups": 709,
        },
        "irdff_sp_mat9861_709": {
            "source": {"archive": str(IRDFF_SP), "archive_sha256": sha256_file(IRDFF_SP),
                       "member": "IRDFF-II_sp.g", "mat": 9861},
            "derivation": {
                "rule": "overlap-conserving histogram collapse onto the fispact-709 "
                        "boundaries carried as 'bounds' in the pinned NPZ",
                "boundary_source": {"file": "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz",
                                    "sha256": sha256_file(ROOT / "actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz"),
                                    "array": "bounds"},
                "amendment": "ACTINV-P26_AMENDMENT_1.md R1",
            },
            "groups": 709,
        },
    }

    matcmp_cases = [
        {"case": f"{m}__{sp}__{sc}", "material": m, "spectrum": sp, "schedule": sc}
        for m in MATERIALS for sp in spectra for sc in SCHEDULES
    ]
    campaign_cases = [
        {"case": f"fe_{el.lower()}{c}wppm__{sp}__{int(d)}s",
         "material_rule": {"base": "FE", "impurity": el, "concentration_wppm": c},
         "spectrum": sp, "schedule_rule": {"irradiation_s": d}}
        for el in CAMPAIGN_IMPURITIES for c in CAMPAIGN_CONCENTRATIONS_WPPM
        for sp in spectra for d in CAMPAIGN_DURATIONS_S
    ]

    contract = {
        "schema": "actinv-p26-g2-contract-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_commit": OPENING_COMMIT,
        "workload_record_sha256": sha256_file(G1_RECORD),
        "selected_workloads": g1["selected"],
        "comparator_set": {
            "actinv_v1_0_1": {"role": "released baseline", "availability": "executable",
                              "identity": "target/release/actinv (v1.0.1 release build)"},
            "actinv_candidate": {"role": "current HEAD / prototype host", "availability": "executable",
                                 "identity": "workspace build at measurement commit"},
            "alara_2_9_2": {"role": "equivalent-output comparator", "availability": "executable",
                            "identity": "/home/connoravila/nuclear-data/alara-2.9.2-build/src/alara",
                            "source_commit": "faa5b330460fe865e38fc788f1b792ea33d13d1b"},
            "njoy_2016_79": {"role": "data-processing comparator only (no equivalent-output leg)",
                             "availability": "executable",
                             "identity": "/home/connoravila/nuclear-data/njoy2016.79-build/njoy"},
            "fispact_ii": {"role": "documented-only comparator", "availability": "not_available",
                           "identity": "FISPACT-II 5.1 documentation; CB1 public output was 4.0/TENDL-2017"},
            "scale_origen": {"role": "documented-only comparator", "availability": "not_available",
                             "identity": "SCALE 6.3.3 / ORIGEN manual"},
            "openmc": {"role": "documented-only comparator (R2S reference)", "availability": "not_available",
                       "identity": "OpenMC 0.15.3 exercised in CB1; 0.16.0 documented"},
        },
        "comparison_legs": {
            "identical_data": "both tools consume the same nuclear-data inputs; pass/fail on tolerances",
            "product_plus_data": "each tool with its standard data; differences reported, never a solver verdict",
        },
        "workloads": {
            "W-MATCMP": {
                "eligible_population": {
                    "materials": MATERIALS, "spectra": spectra, "schedules": SCHEDULES,
                    "responses": RESPONSES, "cooling_times_s": COOLING_TIMES_S,
                    "cases": matcmp_cases, "case_count": len(matcmp_cases),
                },
                "equivalent_output": {
                    "fields": RESPONSES,
                    "units": {"total_activity_bq": "Bq", "decay_heat_w": "W",
                              "photon_source_per_group": "photons/s per source group"},
                    "attribution": "same set of nuclides in top-5 by activity at each cooling time",
                },
            },
            "W-CAMPAIGN": {
                "eligible_population": {
                    "generation_rule": "cartesian: 10 impurity elements x 10 concentrations x 2 spectra x 5 durations",
                    "impurity_elements": CAMPAIGN_IMPURITIES,
                    "concentrations_wppm": CAMPAIGN_CONCENTRATIONS_WPPM,
                    "durations_s": CAMPAIGN_DURATIONS_S,
                    "spectra": list(spectra),
                    "responses": RESPONSES, "cooling_times_s": COOLING_TIMES_S,
                    "cases": campaign_cases, "case_count": len(campaign_cases),
                },
                "equivalent_output": {"fields": RESPONSES,
                                      "units": {"total_activity_bq": "Bq", "decay_heat_w": "W"}},
            },
            "W-R2S": {
                "eligible_population": {
                    "mesh_source": {"file": "examples/mesh_flux.ndjson",
                                    "sha256": sha256_file(ROOT / "examples" / "mesh_flux.ndjson"),
                                    "cells": 3},
                    "responses": ["per_cell_inventory", "per_cell_photon_source_per_group",
                                  "source_normalization"],
                    "cooling_times_s": COOLING_TIMES_S,
                },
                "equivalent_output": {
                    "fields": ["per_cell_inventory", "per_cell_photon_source_per_group",
                               "source_normalization"],
                    "note": "no executable R2S comparator on this workstation; the leg is "
                            "predeclared comparator_unavailable and the definition stands for "
                            "any later lawful comparator run",
                },
            },
        },
        "tolerances": {
            "identical_data": {
                "inventory_relative": 5.0e-4,
                "activity_relative": 5.0e-4,
                "decay_heat_relative": 5.0e-4,
                "photon_group_relative": {"tolerance": 5.0e-4,
                                          "applies_to_groups": ">= 1e-6 of total emission"},
                "top5_set": "same nuclide set required",
            },
            "product_plus_data": {"note": "no pass bound; differences reported and labeled"},
        },
        "measurement_rules": {
            "wall_time_scope": "complete path: input parse + data load + collapse + solve + requested outputs",
            "cache_states": ["cold", "warm"], "report_both": True,
            "resource_limits": {"MemoryMax": "6G", "MemorySwapMax": 0, "TasksMax": 128,
                                "CPUQuota": "200%", "jobs_at_once": 1},
            "tmpdir": "disk-backed, target/preflight-tmp inside the bounded scope",
            "context_required": ["hardware", "input identities", "cache state", "versions",
                                 "thread limits", "preparation amortization"],
            "headroom_ratio": "comparator_complete_wall_time / actinv_complete_wall_time on the "
                              "complete campaign; reportable only where equivalent-output holds "
                              "(identical_data) or as labeled product_plus_data",
            "timeout_budgets": {"per_case_s": 1800, "per_campaign_s": 28800},
            "draft_targets_under_test": {"primary": "10x complete-campaign wall-time headroom",
                                        "secondary": "3x on the second distinct workload",
                                        "hands_on": "50% median hands-on reduction — no recorded "
                                                    "baseline; remains undetermined"},
        },
        "failure_categories": [
            "actinv_error", "comparator_error", "comparator_timeout", "unsupported_input",
            "output_mismatch", "contract_gap", "comparator_unavailable", "budget_exceeded",
        ],
        "evidence_partitions": {
            "diagnostic": {"consumers": ["prototype iteration, profiling"],
                           "consumption": "unlimited within P26"},
            "p26_qualifying": {"consumers": ["P26 G3 measurements feeding the verdict"],
                               "consumption": "once, at G3"},
            "reserved": {"consumers": ["no blind measurement partition exists for P26; all "
                                       "benchmark corpora were consumed by earlier phases"],
                         "consumption": "none in this phase"},
            "measurement_class_feeds": {
                "wall_time": "p26_qualifying (declared runs) + diagnostic (iteration)",
                "hands_on_decomposition": "diagnostic only — no practitioner study recorded",
                "executable_coverage_census": "p26_qualifying",
            },
        },
    }
    OUT.write_text(json.dumps(contract, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"matcmp_cases": len(matcmp_cases), "campaign_cases": len(campaign_cases),
                      "comparators_executable": 3}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
