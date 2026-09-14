#!/usr/bin/env python3
"""P25c G0 independent checker.

Imports no production module and no seal module.  Rehashes the
protocol, re-verifies the opening-commit ancestry, independently
rebuilds the TENDL-2025 neutron corpus manifest, re-derives the sealed
81-file population from the P25 census and rehashes each source file,
re-verifies every prior verdict verbatim, re-checks release state live,
and rejects planted mutations of the seal record.

Exit status is nonzero on any failure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
RECORD = RESULTS / "g0_p25c_seals.json"
PROTOCOL = REPO / "protocols" / "ACTINV-P25c_PROTOCOL.md"
PROTOCOL_SHA256 = "7e42d73c759ee5ad663d65fee71b512e1904fac1e1dacc617dc32dc95edbfa05"
OPENING_COMMIT = "c4f6b4daaad542e9c160d427cedf8fc25019a5e5"

SOURCE_ROOT = Path.home() / "nuclear-data" / "tendl-2025" / "files" / "n"
PATCHED_ROOT = Path.home() / "nuclear-data" / "tendl-2025-patched" / "files" / "n"
CENSUS = RESULTS / "g1_p25_census.json"
CATALOG_MANIFEST = (
    REPO / "crates" / "actinv-cli" / "data" / "actinv-data-catalog-v1.0.0.json"
)

VERDICTS = {
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
    "verdict_p26.json": "P26-FAIL",
    "verdict_p25b.json": "P25b-FAIL",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_report(record: dict) -> list[str]:
    local = []
    if record.get("pass") is not True:
        local.append("record pass field")
    if record.get("schema") != "actinv-p25c-g0-seals-1":
        local.append("schema")
    if record.get("protocol_sha256") != PROTOCOL_SHA256:
        local.append("protocol hash in record")
    if record.get("opening_commit") != OPENING_COMMIT:
        local.append("opening commit in record")
    src = record.get("source_corpus", {})
    if src.get("file_count") != 2850:
        local.append(f"source file_count {src.get('file_count')}")
    man = Path(src.get("manifest_file", ""))
    if not man.is_file() or sha256(man) != src.get("manifest_sha256"):
        local.append("source manifest hash")
    pop = record.get("sealed_population", {})
    if pop.get("count") != 81 or len(pop.get("files", {})) != 81:
        local.append("sealed population count")
    for name, det in pop.get("files", {}).items():
        if not det.get("sha_matches_census"):
            local.append(f"{name} sha mismatch vs census")
        if det.get("source_sha256") != sha256(SOURCE_ROOT / name):
            local.append(f"{name} live source hash")
    if not record.get("prior_verdicts_ok"):
        local.append("prior_verdicts_ok false")
    for name, want in VERDICTS.items():
        pv = record.get("prior_verdicts", {}).get(name, {})
        if pv.get("verdict") != want or not pv.get("ok"):
            local.append(f"recorded {name} != {want}")
    for name, h in record.get("prior_evidence_sha256", {}).items():
        if h != sha256(RESULTS / name):
            local.append(f"evidence hash {name}")
    lic = record.get("license", {})
    if "derived corpus permitted" not in str(lic.get("derivative_policy", "")):
        local.append("license derivative policy absent")
    rs = record.get("release_state", {})
    if rs.get("data_v1_1_tags"):
        local.append("data-v1.1* tag recorded")
    if rs.get("patched_corpus_preexists"):
        local.append("patched corpus preexists")
    if rs.get("shipped_catalog_manifest_sha256") != sha256(CATALOG_MANIFEST):
        local.append("shipped catalog manifest hash")
    return local


def main() -> int:
    failures: list[str] = []
    if sha256(PROTOCOL) != PROTOCOL_SHA256:
        failures.append("protocol sha256 mismatch")
    if subprocess.run(
            ["git", "merge-base", "--is-ancestor", OPENING_COMMIT, "HEAD"],
            cwd=REPO).returncode != 0:
        failures.append("opening commit is not an ancestor of HEAD")

    record = json.loads(RECORD.read_text())

    # live re-derivations
    for name, want in VERDICTS.items():
        path = RESULTS / name
        if not path.exists():
            failures.append(f"missing verdict {name}")
        elif json.loads(path.read_text()).get("verdict") != want:
            failures.append(f"{name} verdict mismatch")

    if subprocess.run(["git", "tag", "-l", "data-v1.1*"], cwd=REPO,
                      capture_output=True, text=True).stdout.strip():
        failures.append("data-v1.1* tag exists")
    if PATCHED_ROOT.exists():
        failures.append("patched corpus exists before G2")

    live_manifest = {}
    for path in sorted(SOURCE_ROOT.glob("*.tendl")):
        live_manifest[path.name] = sha256(path)
    src = record.get("source_corpus", {})
    man = Path(src.get("manifest_file", ""))
    recorded = {}
    if man.is_file():
        for line in man.read_text().splitlines():
            h, _, n = line.partition("  ")
            recorded[n.strip()] = h
    if recorded != live_manifest:
        failures.append("source manifest diverged from live corpus")

    census = json.loads(CENSUS.read_text())
    live_pop = {
        n for n, r in census["file_failures"]["neutron"].items()
        if r.get("class") == "conservation_excess_untraced"
    }
    rec_pop = set(record.get("sealed_population", {}).get("files", {}))
    if live_pop != rec_pop:
        failures.append(
            f"sealed population drifted: only-live={sorted(live_pop - rec_pop)} "
            f"only-recorded={sorted(rec_pop - live_pop)}")

    failures.extend(check_report(record))

    mutations = rejected = 0
    plants = [
        lambda r: r.update({"pass": False}),
        lambda r: r["prior_verdicts"].update(
            {"verdict_p25.json": {"verdict": "P25-PASS", "ok": True}}),
        lambda r: r["release_state"].update({"data_v1_1_tags": ["data-v1.1.0"]}),
        lambda r: r["sealed_population"]["files"].popitem(),
        lambda r: r.update({"protocol_sha256": "0" * 64}),
        lambda r: r["source_corpus"].update({"file_count": 2849}),
        lambda r: r["release_state"].update({"patched_corpus_preexists": True}),
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
        "schema": "actinv-p25c-g0-check-1",
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": PROTOCOL_SHA256,
        "mutation_self_test": {"planted": mutations, "rejected": rejected},
    }
    out = RESULTS / "g0_p25c_check.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
