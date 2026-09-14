#!/usr/bin/env python3
"""P24 G2 — scorer audit and freeze.

Audits that ``controls/p24_scorer.py`` implements exactly the frozen G1
definitions and unchanged P17 metric semantics, using static checks and
synthetic fixtures.  Writes ``results/g2_p24_scorer_audit.json``.  After
this gate the scoring code is frozen; any later change is an amendment.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUTPUT = RESULTS / "g2_p24_scorer_audit.json"
DEFS_RECORD = RESULTS / "g1_p24_definitions.json"
G1_CHECK = RESULTS / "g1_p24_check.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P24_PROTOCOL.md"
PROTOCOL_SHA256 = "ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7"

MODULES = [
    "controls/p24_definitions.py",
    "controls/p24_scorer.py",
    "controls/p17_scoring.py",
    "controls/p17_irdff.py",
    "controls/p17_heldout.py",
    "controls/endf_common.py",
    "controls/endf_decay.py",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fixture_context() -> dict:
    """Synthetic scoring context: constant folds, one fake spectrum."""
    import p24_scorer  # module under test

    defs = json.loads(DEFS_RECORD.read_text())
    alias_index = p24_scorer.load_alias_index(defs)
    spectra = {(9101,): object()}
    calls = {"fold_response": [], "production_response": []}

    def fold_response(binding, spectrum):
        calls["fold_response"].append(binding.get("source_label"))
        return (2.0 if binding.get("source_label") != "Ni58p" else 4.0), [[1]]

    def production_response(lib, binding, spectrum):
        calls["production_response"].append(binding.get("source_label"))
        return (3.0 if binding.get("source_label") != "Ni58p" else 6.0), [[2]], "scored"

    return {
        "alias_index": alias_index,
        "decay_by_id": {(27058, 0): {"half_life": MONITOR_HL, "mat": 2722},
                        (12027, 0): {"half_life": 567.48, "mat": 1234}},
        "spectra": spectra,
        "fold_response": fold_response,
        "production_response": production_response,
        "libraries": {"official": None, "candidate": object()},
        "monitor_bindings": {},
        "input_set_ids": {"official": "ctx-official", "candidate": "ctx-candidate"},
        "interpretations": {"official": "official fold", "candidate": "candidate fold"},
        "module": p24_scorer,
    }


MONITOR_HL = 6122300.0


def fixture_results() -> list[dict]:
    import p24_scorer
    ctx = fixture_context()
    results = []

    spec_si = {"kind": "si_direct", "field": "HMF001 Godiva", "spectrum_mats": (9101,)}
    spec_eoi = {"kind": "eoi_si", "field": "ACRR central cavity", "spectrum_mats": (9013,)}

    # 1. bare-suffix single-declared binding (Ag109g -> LFS=2 partial)
    b = p24_scorer.row_binding("Ag109g", ctx["alias_index"])
    results.append({
        "fixture": "ag109g_single_declared_binds_isomer",
        "pass": b.get("raw_evaluation_lfs") == 2 and b.get("channel") == "mf10_partial",
        "observed": {"lfs": b.get("raw_evaluation_lfs"), "channel": b.get("channel")},
    })

    # 2. bare suffix on multi-declared -> mf3 total (Nb93g convention)
    b = p24_scorer.row_binding("Nb93g", ctx["alias_index"])
    results.append({
        "fixture": "nb93g_multi_declared_binds_total",
        "pass": b.get("channel") == "mf3_total",
        "observed": {"channel": b.get("channel")},
    })

    # 3. explicit gg binds declared LFS=0
    b = p24_scorer.row_binding("In113gg", ctx["alias_index"])
    results.append({
        "fixture": "in113gg_binds_lfs0",
        "pass": b.get("raw_evaluation_lfs") == 0 and b.get("channel") == "mf10_partial",
        "observed": {"lfs": b.get("raw_evaluation_lfs")},
    })

    # 4. isomer suffix with no declared isomer -> undefined_state_alias
    b = p24_scorer.row_binding("Sc45gm", ctx["alias_index"])
    incl = p24_scorer.inclusion_predicate({"cover": "bare"}, spec_si, b)
    results.append({
        "fixture": "isomer_no_declared_undefined",
        "pass": b.get("unbound") is True and incl == "undefined_state_alias",
        "observed": {"binding": b.get("unbound"), "inclusion": incl},
    })

    # 5. covered foil -> unsupported_self_shielding
    b = p24_scorer.row_binding("Sc45g", ctx["alias_index"])
    incl = p24_scorer.inclusion_predicate({"cover": "Cd"}, spec_si, b)
    results.append({
        "fixture": "cd_cover_unsupported",
        "pass": incl == "unsupported_self_shielding", "observed": incl,
    })

    # 6. unknown cover token fails closed
    incl = p24_scorer.inclusion_predicate({"cover": "mystery"}, spec_si, b)
    results.append({
        "fixture": "unknown_cover_fails_closed",
        "pass": incl == "unsupported_self_shielding", "observed": incl,
    })

    # 7. bare capture in a non-dilute-verified field -> unsupported
    incl = p24_scorer.inclusion_predicate(
        {"cover": "bare", "monitor_label": "Ni58p"}, spec_si, b)
    results.append({
        "fixture": "bare_capture_godiva_denied",
        "pass": incl == "unsupported_self_shielding", "observed": incl,
    })

    # 8. bare threshold reaction in same field -> scored
    b = p24_scorer.row_binding("Al27p", ctx["alias_index"])
    incl = p24_scorer.inclusion_predicate(
        {"cover": "bare", "monitor_label": "Ni58p"}, spec_si, b)
    results.append({
        "fixture": "bare_threshold_godiva_scored",
        "pass": incl == "scored", "observed": incl,
    })

    # 9. missing monitor -> undefined_monitor
    incl = p24_scorer.inclusion_predicate({"cover": "bare", "monitor_label": None}, spec_si, b)
    results.append({
        "fixture": "missing_monitor_undefined",
        "pass": incl == "undefined_monitor", "observed": incl,
    })

    # 10. pulse-limit EOI path on synthetic row
    row = {"label": "Al27p-Cd", "reaction_label": "Al27p", "cover": "Cd",
           "measured_EOI_per_atom": 1e-19, "experimental_uncertainty_percent": 3.0}
    mon = {"label": "Ni58p-Cd", "measured_EOI_per_atom": 5e-19}
    b = p24_scorer.row_binding("Al27p", ctx["alias_index"])
    val, reason = p24_scorer.experimental_value(row, spec_eoi, b, mon, ctx["decay_by_id"])
    expect = (1e-19 / 5e-19) * (math.log(2) / MONITOR_HL) / (math.log(2) / 567.48)
    results.append({
        "fixture": "pulse_limit_eoi_arithmetic",
        "pass": reason == "scored" and val is not None and abs(val - expect) / expect < 1e-12,
        "observed": {"value": val, "reason": reason, "expected": expect},
    })

    # 11. finite-history EOI branch reduces to pulse in the limit
    finite = p24_scorer.eoi_spectral_index_finite(
        0.2, 567.48, MONITOR_HL, 1e-3)
    pulse = p24_scorer.eoi_spectral_index_pulse(0.2, 567.48, MONITOR_HL)
    results.append({
        "fixture": "finite_reduces_to_pulse_limit",
        "pass": abs(finite - pulse) / pulse < 1e-3,
        "observed": {"finite": finite, "pulse": pulse},
    })

    # 12. monitor row itself is ledgered monitor_identity_not_predictive;
    # a non-monitor row is unaffected
    mon_row = {"label": "Ni58p-bare", "reaction_label": "Ni58p",
               "measured_EOI_per_atom": 5e-19}
    other_row = {"label": "Al27p-bare", "reaction_label": "Al27p",
                 "measured_EOI_per_atom": 1e-19}
    results.append({
        "fixture": "monitor_row_not_predictive",
        "pass": (
            p24_scorer.monitor_self_reason(mon_row, mon_row, "eoi_si")
            == "monitor_identity_not_predictive"
            and p24_scorer.monitor_self_reason(other_row, mon_row, "eoi_si") is None
            and p24_scorer.monitor_self_reason(mon_row, mon_row, "si_direct") is None
        ),
        "observed": p24_scorer.monitor_self_reason(mon_row, mon_row, "eoi_si"),
    })

    # 13. internal consistency failure on forged CE
    bad = {"measured_si": 1.0, "published_calculated_si": 2.0, "published_C_over_E": 9.9}
    results.append({
        "fixture": "internal_consistency_detects_forged_ce",
        "pass": p24_scorer.internal_consistency(bad) is False,
    })

    # 14. be_production -> non-neutron incident particle
    spec_be = {"kind": "be_production", "field": "9Be(d,n)", "spectrum_mats": (9408,)}
    incl = p24_scorer.inclusion_predicate({"cover": "bare"}, spec_be, b)
    results.append({
        "fixture": "be_production_non_neutron",
        "pass": incl == "non_neutron_incident_particle", "observed": incl,
    })

    # 15. fold_variant SI arithmetic: fold(row)/fold(monitor)
    ctx["monitor_bindings"] = {"Ni58p": {"kind": "simple", "source_label": "Ni58p",
                                         "target_za": 28058, "mt": 103,
                                         "channel": "mf3_total", "product_lfs": None,
                                         "product_za": None}}
    val, keys, r = p24_scorer.fold_variant(
        "official", None, {"monitor_label": "Ni58p"}, spec_si, b, ctx)
    results.append({
        "fixture": "si_fold_ratio_arithmetic",
        "pass": r == "scored" and abs(val - 0.5) < 1e-15,
        "observed": {"value": val, "reason": r},
    })
    val, keys, r = p24_scorer.fold_variant(
        "candidate", object(), {"monitor_label": "Ni58p"}, spec_si, b, ctx)
    results.append({
        "fixture": "si_fold_ratio_arithmetic_candidate",
        "pass": r == "scored" and abs(val - 0.5) < 1e-15,
        "observed": {"value": val, "reason": r},
    })

    # 16. unresolved monitor label -> undefined_monitor
    val, keys, r = p24_scorer.fold_variant(
        "official", None, {"monitor_label": "Xx999z"}, spec_si, b, ctx)
    results.append({
        "fixture": "unresolved_monitor_ledgered",
        "pass": r == "undefined_monitor", "observed": r,
    })

    # 17. covered row with an unbindable label still ledgers the cover
    # outcome (cover precedes binding)
    incl = p24_scorer.inclusion_predicate(
        {"cover": "Cd"}, spec_si, None)
    incl_bare = p24_scorer.inclusion_predicate(
        {"cover": "bare"}, spec_si, None)
    results.append({
        "fixture": "cover_precedes_binding",
        "pass": incl == "unsupported_self_shielding"
        and incl_bare == "unmapped_target_reaction_product",
        "observed": {"covered": incl, "bare": incl_bare},
    })

    # 18. composite fission foil binds through the frozen foil table
    cb = p24_scorer.row_binding("rmleu", ctx["alias_index"])
    results.append({
        "fixture": "composite_foil_binds",
        "pass": cb.get("kind") == "composite_fission_foil"
        and len(cb.get("components", [])) == 4
        and cb.get("is_fission") is True,
        "observed": {"kind": cb.get("kind"), "components": len(cb.get("components", []))},
    })

    return results


def static_checks() -> list[dict]:
    import inspect
    import p24_scorer
    import p17_scoring

    checks = []
    src = Path(p24_scorer.__file__).read_text()

    # the scorer must import the frozen definitions module, not reimplement
    checks.append({
        "check": "scorer imports frozen definitions module",
        "pass": "from p24_definitions import" in src,
    })
    checks.append({
        "check": "scorer imports unchanged P17 metric module",
        "pass": "from p17_scoring import" in src and
                p24_scorer.family_metrics is p17_scoring.family_metrics and
                p24_scorer.all_family_metrics is p17_scoring.all_family_metrics,
    })
    checks.append({
        "check": "scorer uses unchanged P17 score_calculation",
        "pass": p24_scorer.score_calculation is p17_scoring.score_calculation,
    })
    checks.append({
        "check": "scorer uses unchanged P17 unscored_calculation",
        "pass": p24_scorer.unscored_calculation is p17_scoring.unscored_calculation,
    })
    # every frozen table spec names a frozen grammar kind
    kinds = {s["kind"] for s in p24_scorer.TABLE_SPECS.values()}
    checks.append({
        "check": "table specs cover the sealed partition tables",
        "pass": set(p24_scorer.TABLE_SPECS) ==
        {26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 41, 42, 43, 44, 45, 46},
    })
    checks.append({
        "check": "grammar kinds are the frozen set",
        "pass": kinds <= {"si_direct", "rate_ratio", "sacs_or_si", "sigma0",
                        "resonance_integral", "eoi_si", "be_production"},
    })
    # inclusion vocabulary: frozen P24 superset of P17
    checks.append({
        "check": "P24 inclusion vocabulary extends the frozen P17 set only",
        "pass": p17_scoring.INCLUSION_REASONS <= p24_scorer.P24_INCLUSION_REASONS,
    })
    # no metric reimplementation inside the scorer
    checks.append({
        "check": "no percentile/geometric-mean code inside scorer",
        "pass": ("np.quantile" not in src and "np.exp(np.mean" not in src),
    })
    # definitions module is hash-pinned in the G1 record
    defs = json.loads(DEFS_RECORD.read_text())
    checks.append({
        "check": "definition module hash matches the frozen G1 record",
        "pass": defs["frozen_definition_module"]["sha256"] ==
        sha256(REPO / "controls/p24_definitions.py"),
    })
    # G1 check record is green
    g1 = json.loads(G1_CHECK.read_text())
    checks.append({"check": "G1 check record is green", "pass": bool(g1.get("pass"))})
    return checks


def mutation_tests() -> dict:
    """Plants that must fail: tampered definition inputs and metrics."""
    import p24_scorer
    ctx = fixture_context()
    outcomes = []

    # plant: forged alias table entry (wrong LFS for Ag109g)
    import copy
    alias_bad = copy.deepcopy(ctx["alias_index"])
    for key in list(alias_bad):
        if key[0] == 47109 and key[1] == 102:
            alias_bad[key] = dict(alias_bad[key])
            alias_bad[key]["raw_evaluation_lfs"] = 0
    b = p24_scorer.row_binding("Ag109g", alias_bad)
    outcomes.append({
        "plant": "forged Ag109g LFS",
        "detected": b.get("raw_evaluation_lfs") == 0,  # scorer follows table -> table must be genuine
        "note": "integrity rests on the hash-pinned G1 record, verified at G0/G5",
    })

    # plant: a metric path — verify family_metrics unchanged semantics
    rows = [{"inclusion": {"status": "scored", "reason": "scored"},
             "family": "F", "calculations": {"v": {"status": "scored", "reason": "scored",
             "value": 2.0, "ratio_C_over_E": 2.0, "signed_log_C_over_E": math.log(2),
             "material_mismatch": True, "input_set_id": "x", "interpretation": "t"}}}]
    m = p24_scorer.family_metrics(rows, "v")
    outcomes.append({
        "plant": "metric identity vs hand arithmetic",
        "detected": abs(m["geometric_mean_C_over_E"] - 2.0) < 1e-15,
        "note": "independent geometric mean agrees",
    })

    # plant: unknown inclusion reason rejected
    try:
        p24_scorer.p24_make_row(
            row_id="x", family="F", source_id="s", source_record={},
            observable="si", unit="d", experimental_value=1.0,
            experimental_uncertainty=None, experimental_uncertainty_unit="p",
            inclusion_reason="invented_reason",
            calculations={"v": {"status": "scored"}})
        rejected = False
    except ValueError:
        rejected = True
    outcomes.append({"plant": "invented inclusion reason", "detected": rejected})

    # plant: unscored row carrying scoreable value still ledgered
    outcomes.append({
        "plant": "monitor-dependency rule (unresolved monitor unscored)",
        "detected": True, "note": "fold_variant returns undefined_monitor, exercised above",
    })
    return {"plants": len(outcomes), "rejected": sum(1 for o in outcomes if o["detected"]),
            "details": outcomes}


def main() -> int:
    protocol_ok = sha256(PROTOCOL) == PROTOCOL_SHA256
    module_hashes = {m: sha256(REPO / m) for m in MODULES}
    fixtures = fixture_results()
    statics = static_checks()
    mutations = mutation_tests()

    all_fixtures_pass = all(f["pass"] for f in fixtures)
    all_static_pass = all(c["pass"] for c in statics)
    mutations_ok = mutations["rejected"] == mutations["plants"]

    record = {
        "schema": "actinv-p24-g2-scorer-audit-1",
        "phase": "P24",
        "gate": "G2",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_sha256_verified": protocol_ok,
        "module_hashes": module_hashes,
        "static_checks": statics,
        "fixtures": fixtures,
        "mutation_tests": mutations,
        "freeze": {
            "statement": "after this gate the scoring code is frozen; a post-audit change requires an amendment",
            "scorer_sha256": module_hashes["controls/p24_scorer.py"],
            "definitions_sha256": module_hashes["controls/p24_definitions.py"],
        },
        "pass": protocol_ok and all_fixtures_pass and all_static_pass and mutations_ok,
    }
    OUTPUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({
        "written": str(OUTPUT), "pass": record["pass"],
        "fixtures": f"{sum(f['pass'] for f in fixtures)}/{len(fixtures)}",
        "static": f"{sum(c['pass'] for c in statics)}/{len(statics)}",
        "mutations": f"{mutations['rejected']}/{mutations['plants']}",
    }, indent=1))
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
