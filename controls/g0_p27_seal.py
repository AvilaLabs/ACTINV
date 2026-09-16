#!/usr/bin/env python3
"""P27 G0 — opening seal, identity pins, executable-scope freeze.

Binds the frozen P27 protocol hash, the opening commit, all prior
verdicts asserted verbatim (including ``P26b-CONDITIONAL`` with its
terms), and the tool identities the phase depends on: the ACTINV binary
at the opening commit, the maintainer's Avila Core checkout HEAD and
binary, the Core semantic-profile string, and the draft schema set the
interchange consumes.  Freezes the executable scope before any product
code lands: the qualified-operations matrix, the contract-family gating
table, the smoke-study population, the adversarial battery and the
``p27_qualifying`` evidence partition.

Writes ``results/g0_p27_seals.json``.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "results" / "g0_p27_seals.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P27_PROTOCOL.md"
PROTOCOL_SHA256 = "70fef47884e59d70655737346fd1a84c2e415fce50cce0fed9c06b216a7fa73e"
OPENING_COMMIT = "2a6b298c0c9f7ca7a8390acbdd0ff906ce5ce4d6"

EXPECTED_VERDICTS = {
    "verdict_p2.json": "P2-CONDITIONAL",
    "verdict_p3.json": "P3-FAIL",
    "verdict_p3b.json": "P3b-PASS",
    "verdict_p4.json": "P4-FAIL",
    "verdict_p4b.json": "P4b-PASS",
    "verdict_p5.json": "P5-PASS",
    "verdict_p6.json": "P6-CONDITIONAL",
    "verdict_p7.json": "P7-CONDITIONAL",
    "verdict_p8.json": "P8-CONDITIONAL",
    "verdict_p9.json": "P9-CONDITIONAL",
    "verdict_p10.json": "P10-CONDITIONAL",
    "verdict_p11.json": "P11-CONDITIONAL",
    "verdict_p12.json": "P12-CONDITIONAL",
    "verdict_p13.json": "P13-PASS",
    "verdict_p14.json": "P14-CLOSED-BELOW-THRESHOLD",
    "verdict_p15.json": "P15-PASS",
    "verdict_p16.json": "P16-CONDITIONAL",
    "verdict_p17.json": "P17-FAIL",
    "verdict_p18.json": "P18-FAIL",
    "verdict_p18b.json": "P18b-FAIL",
    "verdict_cb1.json": "CB1-COMPLETE",
    "verdict_p19.json": "P19-PASS",
    "verdict_p20.json": "P20-PASS",
    "verdict_p21.json": "P21-PASS",
    "verdict_p22.json": "P22-PASS",
    "verdict_p23.json": "P23-PASS",
    "verdict_p24.json": "P24-CONDITIONAL",
    "verdict_p25.json": "P25-FAIL",
    "verdict_p25b.json": "P25b-FAIL",
    "verdict_p25c.json": "P25c-PASS",
    "verdict_p26.json": "P26-FAIL",
    "verdict_p26b.json": "P26b-CONDITIONAL",
}

CORE_CHECKOUT = Path.home() / "Documents" / "Avila-Labs" / "project-north-star"
CORE_BIN = CORE_CHECKOUT / "target" / "release" / "avila-core"
CORE_SCHEMAS = CORE_CHECKOUT / "schemas"
ACTINV_BIN = ROOT / "target" / "release" / "actinv"
P26B_CONTRACT = ROOT / "results" / "g2_p26b_leg_contract.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def core_head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=CORE_CHECKOUT,
                          capture_output=True, text=True,
                          check=True).stdout.strip()


def main() -> int:
    failures = []

    # ---- protocol + opening commit
    live_proto = sha256_file(PROTOCOL)
    if live_proto != PROTOCOL_SHA256:
        failures.append(f"protocol hash mismatch {live_proto[:12]}")
    head = git_head()
    if head != OPENING_COMMIT:
        failures.append(f"opening commit {head[:12]} != {OPENING_COMMIT[:12]}")

    # ---- prior verdicts verbatim
    prior = {}
    for vf, want in EXPECTED_VERDICTS.items():
        p = ROOT / "results" / vf
        if not p.is_file():
            failures.append(f"missing prior verdict {vf}")
            continue
        got = json.loads(p.read_text()).get("verdict")
        prior[vf] = got
        if got != want:
            failures.append(f"prior verdict {vf} altered: {got}")

    # ---- tool identity pins
    tools = {
        "actinv": {"path": str(ACTINV_BIN),
                   "sha256": sha256_file(ACTINV_BIN)
                   if ACTINV_BIN.is_file() else None},
        "avila_core": {"path": str(CORE_BIN),
                       "sha256": sha256_file(CORE_BIN)
                       if CORE_BIN.is_file() else None,
                       "checkout_head": core_head(),
                       "workspace_version": "0.1.0"},
    }
    if not ACTINV_BIN.is_file():
        failures.append("actinv binary missing")
    if not CORE_BIN.is_file():
        failures.append("avila-core binary missing")

    # ---- Core semantic profile + consumed draft schemas
    semantic_profile = "avila.core/semantic/0.2-draft"
    lib_rs = CORE_CHECKOUT / "crates" / "avila-core-kernel" / "src" / "lib.rs"
    if semantic_profile not in lib_rs.read_text(errors="replace"):
        failures.append("Core semantic profile string not found in kernel")
    schemas = {p.name: sha256_file(p)
               for p in sorted(CORE_SCHEMAS.glob("*.schema.json"))}
    consumed = ["case-package.v0.1-draft.schema.json",
                "execution-receipt.v0.1-draft.schema.json",
                "evidence-claims.v0.2-draft.schema.json",
                "qualification.v0.1-draft.schema.json",
                "contract-amendment.v0.1-draft.schema.json",
                "registry-snapshot.v0.2-draft.schema.json",
                "campaign-report.v0.2-draft.schema.json"]
    for s in consumed:
        if s not in schemas:
            failures.append(f"Core schema missing {s}")

    # ---- frozen executable scope
    # Qualified operations: exactly what prior phases qualify - neutron
    # activation + cooling on a hash-pinned 709-group artifact with the
    # standard response set.  Everything else is explicitly unqualified.
    qualified_operations = {
        "act-study-01": {
            "status": "qualified_at_p27",
            "operations": {
                "activation": {
                    "projectile": "neutron",
                    "spectrum_structures": ["fispact-709"],
                    "data": "hash-pinned artifact + named decay refs",
                    "responses": ["total_activity_bq_per_g",
                                  "decay_heat_w_per_g",
                                  "photon_source_per_group",
                                  "inventory_per_nuclide",
                                  "total_atoms_per_g"],
                },
            },
            "excluded": ["uncertainty", "radiological", "damage",
                         "self_shielding"],
        },
        "act-compare-01": {
            "status": "qualified_at_p27",
            "decision_vocabulary": ["within_rel", "max_rel", "min_rel",
                                    "ratio_band", "rank_equal"],
            "requires": ["declared variation axes",
                         "predeclared decision rule",
                         "eligible-population accounting"],
        },
        "act-robust-01": {"status": "unqualified",
                          "delivering_phase": "P30",
                          "error": "family_not_qualified"},
        "act-refine-01": {"status": "unqualified",
                          "delivering_phase": "P29",
                          "error": "family_not_qualified"},
        "act-source-01": {"status": "unqualified",
                          "delivering_phase": "P32",
                          "error": "family_not_qualified"},
    }

    # Frozen smoke-study population: 2 materials x 2 spectra x 2
    # schedules = 8 cases; exercises both campaign spectra and an
    # impurity axis.  The study schema is designed at G1; the population
    # it must expand to is frozen here.
    p26b = json.loads(P26B_CONTRACT.read_text())
    spectra = sorted(p26b["spectra"].keys())
    smoke_population = {
        "materials": {
            "fe": {"Fe": 100.0},
            "fe_co100wppm": {"Fe": 99.99, "Co": 0.01},
        },
        "spectra": spectra,
        "schedules_s": {"pulse_5min": 300.0, "cont_1d": 86400.0},
        "cooling_times_s": [0.0, 86400.0],
        "expected_cases": 8,
        "responses": ["total_activity_bq_per_g", "decay_heat_w_per_g",
                      "photon_source_per_group", "inventory_per_nuclide",
                      "total_atoms_per_g"],
    }

    adversarial_battery = [
        "incorrect_units", "changed_data", "changed_executable",
        "missing_case", "duplicate_case", "zero_metric",
        "forged_evidence", "stale_evidence", "missing_qualification",
        "altered_limits", "unexpected_schema_field", "revoked_template",
    ]

    record = {
        "schema": "actinv-p27-g0-seals-1",
        "phase": "P27",
        "gate": "G0",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "opening_commit": OPENING_COMMIT,
        "seal_commit": head,
        "protocol_sha256": PROTOCOL_SHA256,
        "prior_verdicts": prior,
        "identity_pins": tools,
        "core_interchange": {
            "semantic_profile": semantic_profile,
            "supported_range": {
                "checkout_head": core_head(),
                "workspace_version": "0.1.0",
            },
            "consumed_schemas": {s: schemas[s] for s in consumed
                                 if s in schemas},
            "all_schema_digests": schemas,
        },
        "qualified_operations": qualified_operations,
        "smoke_population": smoke_population,
        "adversarial_battery": adversarial_battery,
        "evidence_partitions": {
            "p27_qualifying": {
                "sealed_at": "G0",
                "consumers": ["P27 G4 verdict"],
                "consumption": "once, at G4",
            },
            "diagnostic": {
                "consumers": ["probes, iteration"],
                "consumption": "unlimited within P27",
            },
        },
        "open_obligations_carried": {
            "c_practitioner_study": "unestablished - maintainer-side",
            "d_transport_comparator": "unmeasurable - inherited by P32",
        },
        "failures": failures,
        "pass": not failures,
    }
    RESULT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": not failures, "failures": failures,
                      "wrote": str(RESULT.relative_to(ROOT))}, indent=1))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
