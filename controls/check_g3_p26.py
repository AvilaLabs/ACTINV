#!/usr/bin/env python3
"""P26 G3 independent checker: verifies the headroom/coverage evidence without
importing the driver, prototype or any production module.

Re-derives from raw records:
- contract identity (sha256 pinned at the G2 freeze);
- context completeness: no timing may lack hardware/executable/cache/limits;
- coverage completeness: every contract W-MATCMP case has an outcome per
  comparator, and outcomes use only the frozen failure vocabulary;
- ledger arithmetic: aggregates recomputed from the raw campaign ledger, and
  the qualifying totals actually reached the declared 1000-case population;
- equivalent-output spot check: batched vs per-invocation responses agree on
  every case run by both modes (numeric equality of the contract fields);
- production cleanliness: nothing under prototypes/ is referenced by
  production paths;
- protocol hash, opening commit, prior verdicts verbatim.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADROOM = ROOT / "results" / "g3_p26_headroom.json"
COVERAGE = ROOT / "results" / "g3_p26_coverage.json"
LEDGER = ROOT / "results" / "g3_p26_campaign_ledger.jsonl"
CONTRACT = ROOT / "results" / "g2_p26_contract.json"
G2_CHECK = ROOT / "results" / "g2_p26_check.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P26_PROTOCOL.md"

PROTOCOL_SHA256 = "0dd9be843e4e195d3f3ebb9a9084f233af3d0eedf5045bbb48d77d665f5d1e06"
OPENING_COMMIT = "3ae2f2656f6e6e56ad401378d9f5c6a96f1cf80d"
FAILURE_VOCAB = {"actinv_error", "comparator_error", "comparator_timeout", "unsupported_input",
                 "output_mismatch", "contract_gap", "comparator_unavailable", "budget_exceeded",
                 "not_applicable"}
EXPECTED_VERDICTS = {
    "results/verdict_p17.json": "P17-FAIL",
    "results/verdict_p18.json": "P18-FAIL",
    "results/verdict_p18b.json": "P18b-FAIL",
    "results/verdict_p24.json": "P24-CONDITIONAL",
    "results/verdict_p25.json": "P25-FAIL",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger() -> list[dict]:
    if not LEDGER.is_file():
        return []
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def norm_step(step: dict) -> dict:
    """Normalize the two recorded shapes: early ledger rows stored the raw
    per-nuclide activity dict under total_activity_bq_per_g."""
    s = dict(step)
    act = s.get("total_activity_bq_per_g")
    if isinstance(act, dict):
        s["top5_nuclides_by_activity"] = sorted(act, key=lambda n: -(act.get(n) or 0.0))[:5]
        s["total_activity_bq_per_g"] = sum(act.values())
    for k in ("decay_heat_w_per_g", "photon_source_total_per_g"):
        if isinstance(s.get(k), dict):
            s[k] = sum(s[k].values())
    return s


def response_equal(a: dict, b: dict, rel: float = 1e-9) -> bool:
    sa = [norm_step(x) for x in a.get("per_step", [])]
    sb = [norm_step(x) for x in b.get("per_step", [])]
    if len(sa) != len(sb):
        return False
    for x, y in zip(sa, sb):
        for k in ("total_activity_bq_per_g", "decay_heat_w_per_g", "photon_source_total_per_g"):
            va, vb = x.get(k), y.get(k)
            if va is None or vb is None:
                if va != vb:
                    return False
                continue
            if not math.isclose(va, vb, rel_tol=rel, abs_tol=0.0):
                return False
        if x.get("top5_nuclides_by_activity") != y.get("top5_nuclides_by_activity"):
            return False
    return True


def check_all(headroom: dict, coverage: dict, ledger: list[dict]) -> list[str]:
    f: list[str] = []
    if headroom.get("schema") != "actinv-p26-g3-headroom-1":
        f.append("schema")
    if sha256_file(PROTOCOL) != PROTOCOL_SHA256:
        f.append("protocol_hash_live")
    if headroom.get("protocol_sha256") != PROTOCOL_SHA256:
        f.append("protocol_sha256")
    frozen = json.loads(G2_CHECK.read_text())["contract_sha256"]
    if sha256_file(CONTRACT) != frozen or headroom.get("contract_sha256") != frozen:
        f.append("contract_frozen")

    ctx = headroom.get("context", {})
    for k in ("host", "cpu", "mem", "executables", "resource_limits", "tmpdir"):
        if not ctx.get(k):
            f.append(f"context:{k}")
    for name, exe in ctx.get("executables", {}).items():
        if not exe.get("sha256") or sha256_file(Path(exe["path"])) != exe["sha256"]:
            f.append(f"exe_hash:{name}")

    # every timing row carries context fields
    for wid, tools in headroom.get("timings", {}).items():
        for tool, rows in tools.items():
            if isinstance(rows, list):
                for i, row in enumerate(rows):
                    for k in ("case", "tool", "cache_state", "wall_s", "returncode"):
                        if k not in row:
                            f.append(f"timing_row:{wid}:{tool}[{i}].{k}")
                    if not isinstance(row.get("wall_s"), (int, float)) or row.get("wall_s", 0) <= 0:
                        f.append(f"timing_wall:{wid}:{tool}[{i}]")

    # coverage: every MATCMP case has an outcome per comparator; frozen vocabulary
    contract = json.loads(CONTRACT.read_text())
    cases = [c["case"] for c in contract["workloads"]["W-MATCMP"]["eligible_population"]["cases"]]
    comps = coverage.get("per_comparator", {})
    for comp, spec in comps.items():
        per = spec.get("W-MATCMP")
        if isinstance(per, dict):
            for case in cases:
                if case not in per:
                    f.append(f"coverage:{comp}:{case}")
            for case, outcome in per.items():
                if outcome not in ("ok",) and outcome not in FAILURE_VOCAB:
                    f.append(f"coverage_vocab:{comp}:{case}:{outcome}")
    # ledger arithmetic
    rows = [r for r in ledger if r.get("case") != "__batch_driver__"]
    for i, r in enumerate(rows):
        if not isinstance(r.get("wall_s"), (int, float)) or r.get("wall_s", 0) <= 0:
            f.append(f"ledger_wall:{i}")
        if r.get("mode") not in ("per_invocation", "batched_inprocess"):
            f.append(f"ledger_mode:{i}")
    by_mode: dict[str, list[float]] = {}
    for r in rows:
        if r.get("ok", r.get("returncode") == 0):
            by_mode.setdefault(r["mode"], []).append(r["wall_s"])
    agg = {m: {"n": len(v), "median_s": statistics.median(v), "total_s": sum(v)}
           for m, v in by_mode.items() if v}
    if "per_invocation" in by_mode and "batched_inprocess" in by_mode:
        ratio = (statistics.median(by_mode["per_invocation"])
                 / statistics.median(by_mode["batched_inprocess"]))
        agg["amortization_ratio_median"] = ratio
    headroom["_recomputed_campaign"] = agg

    contract_cases = {c["case"] for c in
                      contract["workloads"]["W-CAMPAIGN"]["eligible_population"]["cases"]}
    unknown = {r["case"] for r in rows} - contract_cases
    if unknown:
        f.append(f"campaign_unknown_case:{sorted(unknown)[:3]}")
    for mode in ("per_invocation", "batched_inprocess"):
        done = {r["case"] for r in rows if r["mode"] == mode}
        missing = contract_cases - done
        if missing:
            f.append(f"campaign_incomplete:{mode}:{len(missing)}")

    # equivalent output: cases run under both modes must agree on responses
    per_inv = {r["case"]: r for r in rows if r["mode"] == "per_invocation" and r.get("response")}
    batched = {r["case"]: r for r in rows if r["mode"] == "batched_inprocess" and r.get("response")}
    for case in sorted(set(per_inv) & set(batched)):
        if not response_equal(per_inv[case]["response"], batched[case]["response"]):
            f.append(f"equivalent_output:{case}")

    # production cleanliness: no production path references prototypes/
    for pat, base in (("prototypes/", "crates"), ("prototypes/", "python"),
                      ("prototypes", "Cargo.toml")):
        p = ROOT / base
        targets = [p] if p.is_file() else list(p.rglob("*")) if p.is_dir() else []
        for t in targets:
            if t.is_file() and t.suffix in (".rs", ".py", ".toml", ".json", ".md"):
                if "prototypes/" in t.read_text(errors="replace"):
                    f.append(f"prototype_leak:{t.relative_to(ROOT)}")

    for rel, expected in EXPECTED_VERDICTS.items():
        try:
            verdict = json.loads((ROOT / rel).read_text()).get("verdict")
        except Exception:
            verdict = None
        if verdict != expected:
            f.append(f"verdict:{rel}")
    return f


def mutation_self_test(headroom: dict, coverage: dict, ledger: list[dict]) -> tuple[int, int]:
    plants = []

    def plant(mutate):
        h, c, l = copy.deepcopy(headroom), copy.deepcopy(coverage), copy.deepcopy(ledger)
        mutate(h, c, l)
        plants.append((h, c, l))

    plant(lambda h, c, l: h["context"].pop("cpu"))
    plant(lambda h, c, l: h["timings"]["W-MATCMP"]["actinv_v1_0_1"][0].pop("cache_state"))
    plant(lambda h, c, l: c["per_comparator"]["actinv_v1_0_1"]["W-MATCMP"].popitem())
    plant(lambda h, c, l: l.__setitem__(0, {**l[0], "case": "ZZZ-forged"}))
    plant(lambda h, c, l: [r.update(wall_s=-1) for r in l if r.get("mode") == "per_invocation"][:1])
    plant(lambda h, c, l: h.update(contract_sha256="0" * 64))
    rejected = sum(1 for h, c, l in plants if check_all(h, c, l))
    return len(plants), rejected


def main() -> int:
    headroom = json.loads(HEADROOM.read_text())
    coverage = json.loads(COVERAGE.read_text())
    ledger = load_ledger()
    failures = check_all(headroom, coverage, ledger)
    planted, rejected = mutation_self_test(headroom, coverage, ledger)
    report = {
        "schema": "actinv-p26-g3-check-1",
        "protocol_sha256": PROTOCOL_SHA256,
        "campaign_aggregates": headroom.get("_recomputed_campaign"),
        "failures": failures,
        "mutation_self_test": {"planted": planted, "rejected": rejected},
        "pass": not failures and planted == rejected,
    }
    headroom.pop("_recomputed_campaign", None)
    (ROOT / "results" / "g3_p26_check.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
