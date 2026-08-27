#!/usr/bin/env python3
"""Assemble P10-G7 build, provenance, quality, CI and regression evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load(name: str) -> tuple[Path, dict[str, object]]:
    path = RESULTS / name
    return path, json.loads(path.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(name: str) -> tuple[dict[str, object], dict[str, object]]:
    path, data = load(name)
    return data, {
        "file": name,
        "sha256": sha256(path),
        "pass": bool(data.get("pass")),
    }


def main() -> None:
    builds_path, builds_data = load("g7_p10_builds.json")
    builds = {
        name: {
            "targets": item["targets"],
            "rows": item["rows"],
            "npz_sha256": item["npz_sha256"],
            "index_sha256": item["index_sha256"],
            "builder_fingerprint": item["builder_fingerprint"],
            "cached_identity": item["cached_identity"],
            "unsupported_ledger_entries": item["unsupported_ledger_entries"],
            "convergence_ledger_entries": item["convergence_ledger_entries"],
            "pass": item["pass"],
        }
        for name, item in builds_data["builds"].items()
    }

    eaf_regression, eaf_summary = evidence("g7_p10_eaf_regression.json")
    regression_names = [
        "g7_p10_eaf_product.json",
        "g7_p10_neutron_sources.json",
        "g7_p10_hs278_kink.json",
        "g7_p10_co58_linearization.json",
        "g7_p10_quality.json",
        "ci_end_to_end.json",
        "g1_self_contained.json",
        "check_release_notes.json",
        "check_dependencies.json",
    ]
    regression_evidence = {}
    for name in regression_names:
        _, regression_evidence[name.removesuffix(".json")] = evidence(name)

    expected_verdicts = {
        "verdict_p5.json": "P5-PASS",
        "verdict_p6.json": "P6-CONDITIONAL",
        "verdict_p7.json": "P7-CONDITIONAL",
        "verdict_p8.json": "P8-CONDITIONAL",
        "verdict_p9.json": "P9-CONDITIONAL",
    }
    phase_verdicts = {}
    for name, expected in expected_verdicts.items():
        path, data = load(name)
        actual = data.get("verdict")
        phase_verdicts[name.removesuffix(".json")] = {
            "file": name,
            "sha256": sha256(path),
            "expected": expected,
            "actual": actual,
            "pass": actual == expected,
        }

    required_documentation = {
        "README.md": ["Current version: **v0.5.0**", "TENDL-2025", "P10-CONDITIONAL"],
        "docs/METHOD.md": [
            "production Rust path in `actinv-data`",
            "Finite-dilution self-shielding, probability tables and Bondarenko factors",
        ],
        "docs/DATA.md": ["P10 activation-library provenance", "no licensed FISPACT-II executable was run"],
        "docs/ROADMAP.md": ["P10 is closed **P10-CONDITIONAL**", "P11 — Uncertainty is next, unopened and unhashed"],
        "docs/RELEASE_NOTES_v0.5.md": ["The licensed FISPACT executable was not run", "Finite-dilution unresolved self-shielding"],
    }
    documentation = {}
    for relative, fragments in required_documentation.items():
        text = (ROOT / relative).read_text()
        missing = [fragment for fragment in fragments if fragment not in text]
        documentation[relative] = {"required_fragments": fragments, "missing": missing, "pass": not missing}

    regressions_pass = all(item["pass"] for item in regression_evidence.values()) and all(
        item["pass"] for item in phase_verdicts.values()
    ) and all(item["pass"] for item in documentation.values())
    result = {
        "schema": "actinv-p10-g7-complete-1",
        "gate": "P10-G7",
        "builder_fingerprint": builds_data["builder_fingerprint"],
        "build_evidence": {
            "file": builds_path.name,
            "sha256": sha256(builds_path),
            "pass": bool(builds_data.get("pass")),
        },
        "builds": builds,
        "eaf_regression": {
            **eaf_summary,
            "targets_checked": eaf_regression["targets_checked"],
            "current_rows_checked": eaf_regression["current_rows_checked"],
        },
        "regressions": {
            "documentation": documentation,
            "evidence": regression_evidence,
            "phase_verdicts": phase_verdicts,
            "pass": regressions_pass,
        },
        "pass": bool(builds_data.get("pass"))
        and all(item["pass"] for item in builds.values())
        and eaf_summary["pass"]
        and regressions_pass,
    }
    output = RESULTS / "g7_p10_complete.json"
    output.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    raise SystemExit(0 if result["pass"] else 1)


if __name__ == "__main__":
    main()
