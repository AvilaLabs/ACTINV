#!/usr/bin/env python3
"""P25b G3: repair proposal and acceptance freeze.

Publishes the projected post-repair coverage per candidate derived
strictly from the G1 census and G2 adjudication records, declares
which adjudicated classes are repairable inside this phase's
discipline, and freezes per-candidate coverage floors which
Amendment 1 carries verbatim.  No repair code is written or changed
by this control (protocol: checker green required before repair
code); the sole proposed repair is a bounded re-run of the six
timeout rows at a 600 s bound — a bound revision, not a code or
data change.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

REVISED_BOUND_S = 600.0

# Repairable inside the phase: only the bound revision.
REPAIRABLE = {"bound_limited_timeout"}
NON_REPAIRED = {
    "builder_capability": "mf6_law_-5 fission law is feature work, not a "
                          "bounded repair; rows stay "
                          "construction_failed:capability",
    "builder_interp": "log-log interpolation on non-positive values is a "
                      "builder limitation; the file's data carries no "
                      "oracle defect (floor artifact only)",
    "product_state_conflict": "MF=8/MF=9 conflict is a source-data "
                              "inconsistency; no data repair is permitted",
    "format_misdetect_marker": "files are outside the eligible population; "
                               "recorded format_unsupported permanently",
    "genuine_source_inconsistency": "data defects are never repaired "
                                    "mid-phase",
    "unclassified_build_failure": "no demonstrated mechanism to repair",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    census = json.loads((RESULTS / "g1_p25b_census.json").read_text())
    traces = json.loads((RESULTS / "g2_p25b_traces.json").read_text())

    proposals = {}
    for corpus, hist in census["histograms"].items():
        rows = [t for t in traces["traces"]
                if t["corpus"] == corpus
                and t["g1_class"] != "not_in_target_set"]
        base_ok = hist.get("ok", 0)
        projected = base_ok
        repairable_rows = []
        declared = []
        for t in rows:
            family = t["adjudication"].split(":")[0]
            if family in REPAIRABLE:
                repairable_rows.append(t["target"])
                projected += 1
            else:
                declared.append({
                    "target": t["target"],
                    "adjudication": t["adjudication"],
                    "non_repair_reason": NON_REPAIRED.get(
                        family, "no demonstrated mechanism"),
                })
        proposals[corpus] = {
            "pre_repair_ok": base_ok,
            "target_count": census["target_count"],
            "repairable_rows": repairable_rows,
            "projected_post_repair_ok": projected,
            "coverage_floor": base_ok,
            "non_repaired": declared,
        }

    record = {
        "schema": "actinv-p25b-g3-proposal-1",
        "g1_census_sha256": sha256(RESULTS / "g1_p25b_census.json"),
        "g2_traces_sha256": sha256(RESULTS / "g2_p25b_traces.json"),
        "revised_bound_s": REVISED_BOUND_S,
        "repair_classes": {
            "R3_bound_revision": {
                "scope": "re-run the six bound_limited_timeout rows at "
                         "600 s; no code, data or definition change",
                "rows": {c: p["repairable_rows"]
                         for c, p in proposals.items()},
            },
        },
        "non_repair_classes": NON_REPAIRED,
        "proposals": proposals,
    }
    (RESULTS / "g3_p25b_proposal.json").write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({c: {"floor": p["coverage_floor"],
                          "projected": p["projected_post_repair_ok"],
                          "repairable": p["repairable_rows"]}
                      for c, p in proposals.items()},
                     indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
