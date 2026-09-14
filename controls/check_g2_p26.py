#!/usr/bin/env python3
"""P26 G2 independent checker: verifies the frozen comparison contract without
importing the generator or any production/audit module.

Re-derives from repository files:
- every hash pin (workload record, spectrum sources, mesh source, IRDFF archive);
- population arithmetic (case lists enumerate the declared sets exactly);
- required contract fields (comparators, legs, equivalent-output, tolerances,
  measurement rules, timeout budgets, failure categories, partitions);
- protocol hash, opening commit, prior verdicts verbatim.

The contract SHA-256 recorded here is the freeze anchor later gates rehash.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "results" / "g2_p26_contract.json"
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
REQUIRED_TOP = ["comparator_set", "comparison_legs", "workloads", "tolerances",
                "measurement_rules", "failure_categories", "evidence_partitions",
                "selected_workloads", "workload_record_sha256"]
REQUIRED_MEASUREMENT = ["wall_time_scope", "cache_states", "resource_limits",
                        "headroom_ratio", "timeout_budgets", "context_required"]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_record(rec: dict) -> list[str]:
    f: list[str] = []
    if rec.get("schema") != "actinv-p26-g2-contract-1":
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
    if OPENING_COMMIT not in log or head == OPENING_COMMIT:
        f.append("commit_order")

    for key in REQUIRED_TOP:
        if not rec.get(key):
            f.append(f"missing:{key}")
    for key in REQUIRED_MEASUREMENT:
        if key not in rec.get("measurement_rules", {}):
            f.append(f"missing_measurement:{key}")

    g1 = ROOT / "results" / "g1_p26_workloads.json"
    if not g1.is_file() or sha256_file(g1) != rec.get("workload_record_sha256"):
        f.append("workload_record_sha256")

    w = rec.get("workloads", {})
    for wid in ("W-MATCMP", "W-CAMPAIGN", "W-R2S"):
        if wid not in w:
            f.append(f"workload_missing:{wid}")
    mat = w.get("W-MATCMP", {}).get("eligible_population", {})
    mats, specs, scheds = mat.get("materials", {}), mat.get("spectra", {}), mat.get("schedules", {})
    cases = mat.get("cases", [])
    if len(cases) != len(mats) * len(specs) * len(scheds):
        f.append("matcmp_case_count")
    if mat.get("case_count") != len(cases):
        f.append("matcmp_case_count_field")
    if {c.get("material") for c in cases} - set(mats) or {c.get("spectrum") for c in cases} - set(specs):
        f.append("matcmp_case_members")
    for name, sp in specs.items():
        src = sp.get("source", {})
        if "file" in src and sha256_file(ROOT / src["file"]) != src.get("sha256"):
            f.append(f"spectrum_pin:{src['file']}")
        if "archive" in src and sha256_file(Path(src["archive"])) != src.get("archive_sha256"):
            f.append(f"spectrum_pin:{src['archive']}")
        deriv = sp.get("derivation", {})
        bsrc = deriv.get("boundary_source", {})
        if deriv and ("file" not in bsrc
                      or sha256_file(ROOT / bsrc["file"]) != bsrc.get("sha256")):
            f.append(f"spectrum_derivation_pin:{name}")

    camp = w.get("W-CAMPAIGN", {}).get("eligible_population", {})
    cc = camp.get("cases", [])
    expected = (len(camp.get("impurity_elements", [])) * len(camp.get("concentrations_wppm", []))
                * len(camp.get("spectra", [])) * len(camp.get("durations_s", [])))
    if len(cc) != expected or camp.get("case_count") != len(cc):
        f.append("campaign_case_count")

    r2s = w.get("W-R2S", {}).get("eligible_population", {})
    ms = r2s.get("mesh_source", {})
    if sha256_file(ROOT / ms.get("file", "")) != ms.get("sha256"):
        f.append("r2s_mesh_pin")

    for wid, spec in w.items():
        if not spec.get("equivalent_output", {}).get("fields"):
            f.append(f"equivalent_output:{wid}")

    comps = rec.get("comparator_set", {})
    if not any(v.get("availability") == "executable" and "equivalent-output" in v.get("role", "")
               for v in comps.values()):
        f.append("no_executable_equivalent_comparator")
    for name, v in comps.items():
        if v.get("availability") not in ("executable", "not_available") or not v.get("identity"):
            f.append(f"comparator:{name}")

    tol = rec.get("tolerances", {}).get("identical_data", {})
    if not tol or any(not isinstance(v, (int, float, dict, str)) for v in tol.values()):
        f.append("tolerances")
    if not rec.get("failure_categories"):
        f.append("failure_categories")
    feeds = rec.get("evidence_partitions", {}).get("measurement_class_feeds")
    if not feeds:
        f.append("measurement_class_feeds")
        feeds = {}
    declared = set(rec.get("evidence_partitions", {})) - {"measurement_class_feeds"}
    for cls, target in feeds.items():
        if not any(target.startswith(p) or p in target for p in declared):
            f.append(f"partition_feed:{cls}")

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

    def plant(mutate):
        m = copy.deepcopy(rec)
        mutate(m)
        plants.append(m)

    plant(lambda m: m.pop("tolerances"))
    plant(lambda m: m["workloads"]["W-MATCMP"]["eligible_population"].update(case_count=999))
    plant(lambda m: m["comparator_set"]["alara_2_9_2"].update(availability="licensed"))
    plant(lambda m: m.update(workload_record_sha256="0" * 64))
    plant(lambda m: m["failure_categories"].clear())
    plant(lambda m: m["workloads"]["W-CAMPAIGN"]["eligible_population"]["cases"].pop())
    plant(lambda m: m["evidence_partitions"].pop("measurement_class_feeds"))
    rejected = sum(1 for m in plants if check_record(m))
    return len(plants), rejected


def main() -> int:
    rec = json.loads(RECORD.read_text())
    failures = check_record(rec)
    planted, rejected = mutation_self_test(rec)
    report = {
        "schema": "actinv-p26-g2-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "contract_sha256": sha256_file(RECORD),
        "failures": failures,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
        "pass": not failures and planted == rejected,
    }
    (ROOT / "results" / "g2_p26_check.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
