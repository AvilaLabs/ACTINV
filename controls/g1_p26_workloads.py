#!/usr/bin/env python3
"""P26 G1: derive the ranked candidate workload list from recorded evidence.

Every citation in the emitted record resolves to a recorded source: a file in
this repository (or a maintainer-provided record) whose SHA-256 is pinned in
`recorded_sources`, addressed by an inclusive 1-based line range and an anchor
substring that must appear inside the cited span. No citation text is generated
inside this phase; the prose fields summarize, the citations carry the weight.

Evidence classes are a pure function of the cited path so the independent
checker can re-derive them:

- `executed_evidence` (weight 2): paths under `results/` or `docs/history/` —
  records of measurements or phase work this repository actually executed.
- `documented_record` (weight 1): every other path — roadmap drafts, research
  notes, capability-boundary docs, benchmark interpretation.

Score is the sum of citation weights; rank is descending score. Ties are
resolved by the maintainer-proposal designation recorded in `proposal_designations`,
then by candidate id. The flagship must come from the top score cohort.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "protocols" / "ACTINV-P26_PROTOCOL.md"
OUT = ROOT / "results" / "g1_p26_workloads.json"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

EXECUTED_PREFIXES = ("results/", "docs/history/")

# file, [first_line, last_line] inclusive, anchor substring required in the span
C = lambda f, a, b, anchor: {"file": f, "lines": [a, b], "anchor": anchor}

CANDIDATES = [
    {
        "id": "W-MATCMP",
        "title": "Impurity-sensitive material comparison under a specified spectrum and history",
        "definition": (
            "Compare candidate materials (including low-impurity variants) under a specified "
            "neutron spectrum and irradiation/cooling schedule; report activity, decay heat and "
            "photon production at selected cooling times; identify the nuclides and pathways that "
            "control the conclusion; test whether the conclusion survives declared composition and "
            "nuclear-data choices; package the result for independent reproduction."
        ),
        "citations": [
            C("docs/history/sessions/P12.md", 53, 56, "FNG/ITER activation comparison"),
            C("docs/COMPETITIVE_BENCHMARK.md", 17, 20, "132 FNS experiments"),
            C("docs/ROADMAP.md", 148, 153, "impurity-sensitive material comparison"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 82, 86, "impurity-sensitive alloys"),
        ],
        "disposition": "flagship",
        "rationale": (
            "The maintainer's recorded proposal designates this workload as the flagship, and its "
            "executed-evidence base is a completed fusion-material activation comparison (P12's "
            "scoped FNG/ITER work) plus the executed 132-experiment FNS benchmark — the domain in "
            "which consequential material-comparison decisions are already documented."
        ),
    },
    {
        "id": "W-CAMPAIGN",
        "title": "Repeated material/history variant campaign",
        "definition": (
            "Execute a bounded campaign of material-composition and irradiation-history variants "
            "over a common spectrum family, with per-variant ledgers and aggregated responses; the "
            "draft's candidate scale is 1,000 variants, against an executed 20,000 distinct-spectrum "
            "precedent at mesh scale."
        ),
        "citations": [
            C("results/g3_p21_executed.json", 13, 16, "distinct_spectra"),
            C("docs/ROADMAP.md", 282, 284, "1,000 material/history variants"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 83, 85, "repeated material/history variants"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 37, 37, "Sampler"),
        ],
        "disposition": "selected_second_campaign",
        "rationale": (
            "The recorded proposal calls for two campaign workloads beside the spatial handoff, and "
            "P21's executed 20,000-cell run is direct project history that many-variant execution "
            "is a real workload class. It is the flagship's variant axis generalized into a "
            "campaign; it is not itself the flagship because its recorded evidence is an execution "
            "capability, not a consequential decision."
        ),
    },
    {
        "id": "W-R2S",
        "title": "Spatial neutron-flux to decay-photon-source handoff",
        "definition": (
            "Take a spatially distributed neutron flux from an external transport code, produce "
            "per-cell inventories and a distributed decay-photon source, and hand that source back "
            "for photon transport — the open R2S workflow where ACTINV's reproducible, inspectable "
            "position is recorded as strongest."
        ),
        "citations": [
            C("docs/COMPETITIVE_BENCHMARK.md", 250, 255, "Open, reproducible activation and R2S"),
            C("docs/QUALIFICATION.md", 60, 62, "distributed transport source"),
            C("docs/ROADMAP.md", 152, 153, "flux-to-photon handoff"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 140, 141, "R2S path"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 40, 40, "R2SManager"),
        ],
        "disposition": "selected_spatial_handoff",
        "rationale": (
            "The recorded proposal designates one spatial handoff beside the two campaign "
            "workloads; the benchmark record identifies open reproducible R2S as ACTINV's strongest "
            "position, and QUALIFICATION records the concrete gap (users must construct the "
            "distributed source). It is distinct in kind from the campaign workloads and is the "
            "natural second workload for the draft's 3x headroom leg."
        ),
    },
    {
        "id": "W-SDTR",
        "title": "Shutdown source term and decay heat at cooling times",
        "definition": (
            "Produce post-shutdown activity, decay heat and photon source terms at required cooling "
            "times for safety and waste classification questions."
        ),
        "citations": [
            C("docs/ROADMAP.md", 155, 156, "shutdown source terms"),
            C("docs/COMPETITIVE_BENCHMARK.md", 16, 16, "shutdown inventory"),
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 41, 41, "clearance"),
        ],
        "disposition": "rejected",
        "rationale": (
            "Subsumed: the flagship's recorded definition already carries activity, decay heat and "
            "photon production at selected cooling times. Its distinct remainder — waste clearance "
            "classification — rests on a documented competitor capability, not on recorded demand "
            "evidence for ACTINV."
        ),
    },
    {
        "id": "W-DOSIMETRY",
        "title": "Activation dosimetry and spectrum-index folding",
        "definition": (
            "Fold activation cross sections over measured or simulated fields for dosimetry and "
            "spectrum-characterization observables (SACS, spectral indices, cover-ratio rates)."
        ),
        "citations": [
            C("results/g4_p24_fresh.json", 1, 40, "bound_reaction_labels"),
            C("docs/P17_HELDOUT_VALIDATION.md", 1, 30, "held-out"),
        ],
        "disposition": "rejected",
        "rationale": (
            "The recorded evidence is a validation corpus, not an analyst-decision workload: the "
            "IRDFF-II partitions are fully consumed, so no unspent blind measurement population "
            "exists to qualify a dosimetry claim. Folding itself is already exercised inside the "
            "selected workloads' evidence path."
        ),
    },
    {
        "id": "W-UNC",
        "title": "Uncertainty-aware shielded activation",
        "definition": (
            "Propagate declared nuclear-data and input uncertainties through activation responses, "
            "including regimes where self-shielding is required."
        ),
        "citations": [
            C("docs/COMPETITIVE_EXTENSION_RESEARCH_2026-09-13.md", 69, 69, "self_shielding"),
            C("protocols/ACTINV-P20_PROTOCOL.md", 9, 11, "uncertainty"),
        ],
        "disposition": "rejected",
        "rationale": (
            "The recorded evidence is a capability boundary (shielded activation with uncertainty is "
            "rejected by validation), i.e. a gap inside the flagship's uncertainty axis — not a "
            "standalone workload. Its requirement is carried into W-MATCMP's definition rather than "
            "ranked separately."
        ),
    },
]

PROPOSAL_DESIGNATIONS = {
    "flagship": {
        "candidate": "W-MATCMP",
        "citation": C("docs/ROADMAP.md", 148, 148, "proposed flagship"),
    },
    "spatial_handoff": {
        "candidate": "W-R2S",
        "citation": C("docs/ROADMAP.md", 282, 282, "one spatial handoff"),
    },
    "second_campaign": {
        "candidate": "W-CAMPAIGN",
        "citation": C("docs/ROADMAP.md", 282, 282, "two campaign workloads"),
    },
}

LIMITATIONS = [
    (
        "No practitioner interviews or user studies are recorded in this repository. The roadmap "
        "draft's proposed examination of five to eight practitioners (docs/ROADMAP.md lines "
        "275-280) is unexecuted maintainer work requiring separate authorization; demand and "
        "usability conclusions remain unestablished by design."
    ),
    (
        "Workload ranks therefore rest on executed repository evidence (benchmarks, phase records) "
        "and recorded maintainer documents (the draft proposal, the competitive research note, "
        "capability-boundary docs). A documented comparator capability evidences that a workload "
        "exists in the ecosystem, not that users need it from ACTINV."
    ),
    (
        "The draft's 50% hands-on-time reduction target has no recorded baseline; it remains "
        "undetermined until maintainer-collected evidence exists, per the protocol's undetermined-"
        "is-a-phase-failure rule for feasibility judgments."
    ),
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def span_text(file: str, lines: list[int]) -> str:
    text = (ROOT / file).read_text().splitlines()
    return " ".join(" ".join(text[lines[0] - 1 : lines[1]]).split())


def evidence_class(file: str) -> str:
    return "executed_evidence" if file.startswith(EXECUTED_PREFIXES) else "documented_record"


def main() -> int:
    for cand in CANDIDATES:
        for cit in cand["citations"]:
            cit["class"] = evidence_class(cit["file"])
            span = span_text(cit["file"], cit["lines"])
            assert cit["anchor"] in span, f"{cand['id']}: anchor missing in {cit['file']}:{cit['lines']}"
    for des in PROPOSAL_DESIGNATIONS.values():
        cit = des["citation"]
        cit["class"] = evidence_class(cit["file"])
        span = span_text(cit["file"], cit["lines"])
        assert cit["anchor"] in span, f"designation anchor missing in {cit['file']}:{cit['lines']}"

    weights = {"executed_evidence": 2, "documented_record": 1}
    for cand in CANDIDATES:
        cand["score"] = sum(weights[c["class"]] for c in cand["citations"])
        cand["executed_citations"] = sum(1 for c in cand["citations"] if c["class"] == "executed_evidence")

    ordered = sorted(CANDIDATES, key=lambda c: (-c["score"], -c["executed_citations"], c["id"]))
    top_score = ordered[0]["score"]
    top = [c for c in ordered if c["score"] == top_score]
    flagship = next((c for c in top if c["id"] == PROPOSAL_DESIGNATIONS["flagship"]["candidate"]), top[0])
    for i, cand in enumerate(ordered):
        cand["rank"] = i + 1
        if cand is flagship:
            cand["disposition"] = "flagship"

    sources = sorted({c["file"] for c in CANDIDATES for c in c["citations"]}
                     | {d["citation"]["file"] for d in PROPOSAL_DESIGNATIONS.values()})
    record = {
        "schema": "actinv-p26-g1-workloads-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "protocol_commit": OPENING_COMMIT,
        "evidence_class_rule": {
            "executed_evidence": {"weight": weights["executed_evidence"], "path_prefixes": list(EXECUTED_PREFIXES)},
            "documented_record": {"weight": weights["documented_record"], "path_prefixes": []},
        },
        "selection_rule": (
            "Rank by descending citation-weight score, then descending executed-evidence "
            "citation count, then candidate id. The flagship is the proposal-designated "
            "candidate inside the top score cohort, else the first candidate in that cohort; "
            "the proposal-designated spatial handoff and second campaign are selected "
            "regardless of rank; all other candidates are rejected with recorded rationale."
        ),
        "recorded_sources": {f: sha256_file(ROOT / f) for f in sources},
        "candidates": CANDIDATES,
        "ranked_ids": [c["id"] for c in ordered],
        "proposal_designations": PROPOSAL_DESIGNATIONS,
        "flagship": flagship["id"],
        "selected": {
            "flagship": flagship["id"],
            "second_campaign": PROPOSAL_DESIGNATIONS["second_campaign"]["candidate"],
            "spatial_handoff": PROPOSAL_DESIGNATIONS["spatial_handoff"]["candidate"],
        },
        "user_evidence_limitations": LIMITATIONS,
    }
    OUT.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"flagship": flagship["id"], "ranked": [c["id"] for c in ordered],
                      "scores": {c["id"]: c["score"] for c in ordered}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
