#!/usr/bin/env python3
"""P26 G1 independent checker: verifies the workload-derivation record without
importing the generator or any production/audit module.

Re-derives, from the record's own stated rules and the repository files:
- every citation resolves (file hash, line bounds, anchor inside the span);
- evidence classes follow the recorded path-prefix rule;
- scores, executed-citation counts, ranks and ranked_ids follow the weights;
- the flagship and selected set follow the recorded selection rule;
- every candidate carries at least one citation and a non-empty rationale;
- user-evidence limitations are recorded (honesty requirement);
- protocol hash, opening commit and all prior verdicts verbatim.

Exit status is nonzero on any failure; planted mutations must all be rejected.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "results" / "g1_p26_workloads.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P26_PROTOCOL.md"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"

EXPECTED_VERDICTS = {
    "results/verdict_p17.json": "P17-FAIL",
    "results/verdict_p18.json": "P18-FAIL",
    "results/verdict_p18b.json": "P18b-FAIL",
    "results/verdict_p24.json": "P24-CONDITIONAL",
    "results/verdict_p25.json": "P25-FAIL",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def span_text(file: str, lines: list[int]) -> str | None:
    path = ROOT / file
    if not path.is_file():
        return None
    text = path.read_text().splitlines()
    if lines[0] < 1 or lines[1] > len(text) or lines[0] > lines[1]:
        return None
    return " ".join(" ".join(text[lines[0] - 1 : lines[1]]).split())


def class_for(file: str, rule: dict) -> str | None:
    for name, spec in rule.items():
        if any(file.startswith(p) for p in spec["path_prefixes"]):
            return name
    for name, spec in rule.items():
        if not spec["path_prefixes"]:
            return name
    return None


def check_record(rec: dict) -> list[str]:
    f: list[str] = []

    if rec.get("schema") != "actinv-p26-g1-workloads-1":
        f.append("schema")
    if rec.get("protocol_sha256") != PROTOCOL_SHA256:
        f.append("protocol_sha256")
    if sha256_file(PROTOCOL) != PROTOCOL_SHA256:
        f.append("protocol_hash_live")
    if rec.get("protocol_commit") != OPENING_COMMIT:
        f.append("protocol_commit")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    log = subprocess.run(["git", "log", "--format=%H"], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    if OPENING_COMMIT not in log:
        f.append("opening_commit_not_ancestor")
    if head == OPENING_COMMIT:
        f.append("no_post_opening_commit")

    rule = rec.get("evidence_class_rule", {})
    weights = {k: v.get("weight") for k, v in rule.items()}
    if not weights or any(not isinstance(w, int) or w <= 0 for w in weights.values()):
        f.append("evidence_class_rule")

    for file, digest in rec.get("recorded_sources", {}).items():
        if sha256_file(ROOT / file) != digest:
            f.append(f"recorded_source_hash:{file}")

    candidates = rec.get("candidates", [])
    if not candidates:
        f.append("no_candidates")
    seen_ids: set[str] = set()
    for cand in candidates:
        cid = cand.get("id", "?")
        if cid in seen_ids:
            f.append(f"duplicate_id:{cid}")
        seen_ids.add(cid)
        cits = cand.get("citations", [])
        if not cits:
            f.append(f"no_citations:{cid}")
        if not cand.get("rationale"):
            f.append(f"missing_rationale:{cid}")
        score = 0
        executed = 0
        for i, cit in enumerate(cits):
            file = cit.get("file", "")
            lines = cit.get("lines", [])
            anchor = cit.get("anchor", "")
            if file not in rec.get("recorded_sources", {}):
                f.append(f"citation_unpinned:{cid}[{i}]")
            cls = class_for(file, rule)
            if cls is None or cit.get("class") != cls:
                f.append(f"citation_class:{cid}[{i}]")
            else:
                score += weights[cls]
                executed += cls == "executed_evidence"
            span = span_text(file, lines)
            if span is None or anchor not in span:
                f.append(f"citation_anchor:{cid}[{i}]")
        if cand.get("score") != score:
            f.append(f"score:{cid}")
        if cand.get("executed_citations") != executed:
            f.append(f"executed_citations:{cid}")

    ranked = sorted(candidates,
                    key=lambda c: (-c.get("score", 0), -c.get("executed_citations", 0), c.get("id", "")))
    if [c.get("id") for c in ranked] != rec.get("ranked_ids"):
        f.append("ranked_ids")
    for i, cand in enumerate(ranked):
        if cand.get("rank") != i + 1:
            f.append(f"rank:{cand.get('id')}")

    des = rec.get("proposal_designations", {})
    for name, spec in des.items():
        cit = spec.get("citation", {})
        if spec.get("candidate") not in seen_ids:
            f.append(f"designation_target:{name}")
        span = span_text(cit.get("file", ""), cit.get("lines", []))
        if span is None or cit.get("anchor", "") not in span:
            f.append(f"designation_anchor:{name}")

    top = [c for c in ranked if c.get("score") == ranked[0].get("score")] if ranked else []
    expected_flagship = next(
        (c for c in top if c.get("id") == des.get("flagship", {}).get("candidate")),
        top[0] if top else None,
    )
    if expected_flagship is None or rec.get("flagship") != expected_flagship.get("id"):
        f.append("flagship")
    sel = rec.get("selected", {})
    if sel.get("flagship") != rec.get("flagship"):
        f.append("selected_flagship")
    for key, des_name in (("second_campaign", "second_campaign"),
                          ("spatial_handoff", "spatial_handoff")):
        if sel.get(key) != des.get(des_name, {}).get("candidate"):
            f.append(f"selected_{key}")
    for cand in candidates:
        disp = cand.get("disposition")
        if cand.get("id") == rec.get("flagship"):
            ok = disp == "flagship"
        elif cand.get("id") in {sel.get("second_campaign"), sel.get("spatial_handoff")}:
            ok = disp in ("selected_second_campaign", "selected_spatial_handoff")
        else:
            ok = disp == "rejected"
        if not ok:
            f.append(f"disposition:{cand.get('id')}")

    if not rec.get("user_evidence_limitations"):
        f.append("user_evidence_limitations")

    for rel, expected in EXPECTED_VERDICTS.items():
        try:
            verdict = json.loads((ROOT / rel).read_text()).get("verdict")
        except Exception:
            verdict = None
        if verdict != expected:
            f.append(f"verdict:{rel}")

    return f


def mutation_self_test(rec: dict) -> tuple[int, int]:
    plants = []

    def plant(name, mutate):
        m = copy.deepcopy(rec)
        mutate(m)
        plants.append((name, m))

    plant("anchor_corrupted", lambda m: m["candidates"][0]["citations"][0].update(anchor="ZZZ-no-such-text"))
    plant("lines_shifted", lambda m: m["candidates"][0]["citations"][0].update(lines=[1, 1]))
    plant("hash_forged", lambda m: m["recorded_sources"].update(
        {k: "0" * 64 for k in list(m["recorded_sources"])[:1]}))
    plant("score_inflated", lambda m: m["candidates"][-1].update(score=99))
    plant("rationale_removed", lambda m: m["candidates"][-1].update(rationale=""))
    plant("ranked_swapped", lambda m: m.update(ranked_ids=list(reversed(m["ranked_ids"]))))
    plant("flagship_swapped", lambda m: m.update(flagship="W-UNC"))

    rejected = sum(1 for _, m in plants if check_record(m))
    return len(plants), rejected


def main() -> int:
    rec = json.loads(RECORD.read_text())
    failures = check_record(rec)
    planted, rejected = mutation_self_test(rec)
    report = {
        "schema": "actinv-p26-g1-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "failures": failures,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
        "pass": not failures and planted == rejected,
    }
    (ROOT / "results" / "g1_p26_check.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
