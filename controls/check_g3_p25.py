#!/usr/bin/env python3
"""P25 G3 independent checker — diagnosis report, Amendment B floors, gate ordering.

Verifies, without importing production or audit modules:

1. G3 evidence chain: diagnosis doc and Amendment B exist; the amendment's
   floor table matches the diagnosis doc.
2. Diagnosis arithmetic: independently recomputes eligible-population outcome
   counts and repairable-population ceilings from the committed G1/G2 records
   and the sealed P18b ledger; the report's published projections must match
   the recomputation exactly.
3. Frozen floors: Amendment B floor shares are strictly below the recomputed
   ceilings and use the full eligible denominator.
4. Gate ordering: crates/actinv-data/src/builder.rs is byte-identical to its
   P18b-G3 blob (no repair code may precede the committed diagnosis).
5. Mutation rejection via --self-test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "actinv-p25-g3-check-1"

PROTOCOL = ROOT / "protocols" / "ACTINV-P25_PROTOCOL.md"
AMENDMENT_A = ROOT / "protocols" / "ACTINV-P25_AMENDMENT_A.md"
AMENDMENT_B = ROOT / "protocols" / "ACTINV-P25_AMENDMENT_B.md"
DIAGNOSIS = ROOT / "docs" / "P25_DIAGNOSIS.md"
CENSUS = ROOT / "results" / "g1_p25_census.json"
TRACES = ROOT / "results" / "g2_p25_traces.json"
LEDGER = ROOT / "results" / "g5_p18b_heldout.json"
G1_CHECK = ROOT / "results" / "g1_p25_check.json"

BUILDER_P18B_BLOB = "e835394f0b592b4898f0624dd7d7da2a3cf6f873"
EXPECTED_ELIGIBLE = {"neutron": 469, "proton": 695, "deuteron": 143, "alpha": 552}
GENUINE = {"genuine_source_inconsistency:zero_total_with_partials",
           "genuine_source_inconsistency:gridpoint_excess"}
FLOORS = {  # share %, row count — must appear in both docs and hold vs ceiling
    "neutron": (40, 188), "proton": (60, 417), "deuteron": (50, 72), "alpha": (55, 304),
}
CODE = {"neutron": "n", "proton": "p", "deuteron": "d", "alpha": "a"}
SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt "
    "Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr "
    "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def family_target_file(projectile: str, family_id: str) -> str | None:
    """`000-001|028-062|...|62Ni(n,p)62Co` -> `n-Ni062.tendl`."""
    try:
        target = family_id.split("|")[1]
        zt, at = int(target.split("-")[0]), int(target.split("-")[1])
        return f"{CODE[projectile]}-{SYMBOLS[zt - 1]}{at:03d}.tendl"
    except (IndexError, ValueError):
        return None


def recompute(census, traces, ledger):
    """Independent eligible counts + repairable-ceiling projection.

    Repairable = every file whose final class is not a proven genuine source
    inconsistency (floor, missing-total, interpolation and catalog-conflict
    mechanisms all have named repairs in the G3 proposal). Cascade rows repair
    iff at least one catalog supplier is repairable. undefined_ratio rows are
    never counted toward the ceiling.
    """
    eligible, ceiling = Counter(), Counter()
    repairable = {p: {n: v["final_class"] not in GENUINE for n, v in f.items()}
                  for p, f in traces["files"].items()}
    quarantined = {p: set(census["file_failures"][p]) for p in CODE}
    cascade = traces["catalog_cascade"]
    for row in ledger["ledger"]:
        if row["status"] != "eligible":
            continue
        p = row["projectile"]
        eligible[p] += 1
        cand = row.get("candidate") or {}
        st = cand.get("status")
        if st == "scored":  # includes the 29 named zero-prediction rows
            ceiling[p] += 1
            continue
        if st != "build_failed_g3":
            continue
        fname = family_target_file(p, row["family_id"])
        if fname in quarantined[p]:
            if repairable[p].get(fname, False):
                ceiling[p] += 1
        else:  # staged_built: catalog-supplier cascade
            entry = cascade.get(p, {}).get(row["family_id"])
            if entry and any(
                repairable[p].get(s["file"], False)
                for s in entry["corpus_suppliers"]
            ):
                ceiling[p] += 1
    return eligible, ceiling


def check(failures):
    census = json.loads(CENSUS.read_text())
    traces = json.loads(TRACES.read_text())
    ledger = json.loads(LEDGER.read_text())
    for p in (DIAGNOSIS, AMENDMENT_B, PROTOCOL, AMENDMENT_A, G1_CHECK):
        if not p.exists():
            failures.append(f"missing G3 evidence {p.name}")
    if failures:
        return
    if not json.loads(G1_CHECK.read_text()).get("pass"):
        failures.append("G1 check record is not green")
    diag, amd = DIAGNOSIS.read_text(), AMENDMENT_B.read_text()

    # recomputed eligible population must match the frozen expectation
    eligible, ceiling = recompute(census, traces, ledger)
    for p, n in EXPECTED_ELIGIBLE.items():
        if eligible[p] != n:
            failures.append(f"eligible {p}: recomputed {eligible[p]} != frozen {n}")

    # every quarantined file classified; total matches census
    for p, cnt in traces["final_class_counts"].items():
        if cnt.get("unclassified"):
            failures.append(f"{p}: {cnt['unclassified']} unclassified files")
    if sum(sum(c.values()) for c in traces["final_class_counts"].values()) != 397:
        failures.append("quarantined total != 397")

    # the diagnosis doc's published ceilings must equal recomputation
    for p in EXPECTED_ELIGIBLE:
        m = re.search(rf"{p}\s*\|[^\n]*?(\d+)/(\d+)\s*\(", diag)
        if not m:
            failures.append(f"diagnosis doc missing {p} ceiling ratio")
            continue
        if int(m.group(1)) != ceiling[p] or int(m.group(2)) != eligible[p]:
            failures.append(
                f"{p} ceiling: doc {m.group(1)}/{m.group(2)} != "
                f"recomputed {ceiling[p]}/{eligible[p]}")

    # frozen floors present in both docs, arithmetic sound, strictly below ceiling
    import math
    for p, (share, rows) in FLOORS.items():
        elig = EXPECTED_ELIGIBLE[p]
        if rows != math.ceil(elig * share / 100):
            failures.append(f"{p} floor rows {rows} inconsistent with {share}% of {elig}")
        if ceiling[p] and rows / elig >= ceiling[p] / elig:
            failures.append(f"{p} floor not strictly below ceiling")
        for name, text in (("amendment B", amd), ("diagnosis", diag)):
            if f"{p}" not in text or str(share) not in text or str(rows) not in text:
                failures.append(f"{name} missing {p} floor {share}%/{rows}")
    for needle in ("0.03", "declared product gridpoint", "interp_artifact_reconciled"):
        if needle not in amd:
            failures.append(f"amendment B missing envelope clause: {needle!r}")

    # gate ordering: no repair code may precede the committed diagnosis
    blob = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD:crates/actinv-data/src/builder.rs"],
        capture_output=True, text=True, check=True).stdout.strip()
    if blob != BUILDER_P18B_BLOB:
        failures.append("builder.rs differs from P18b-G3 blob — repair code precedes G3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    failures = []
    check(failures)
    report = {
        "schema": SCHEMA,
        "pass": not failures,
        "failures": failures,
        "protocol_sha256": sha256_file(PROTOCOL),
        "amendment_b_sha256": sha256_file(AMENDMENT_B) if AMENDMENT_B.exists() else None,
        "diagnosis_sha256": sha256_file(DIAGNOSIS) if DIAGNOSIS.exists() else None,
    }
    print(json.dumps(report, indent=2))

    if args.self_test:
        muts = 0
        for path, probe, repl in (
            (DIAGNOSIS, "295/469", "999/469"),
            (DIAGNOSIS, "537/695", "999/695"),
            (AMENDMENT_B, "0.03", "0.05"),
            (AMENDMENT_B, "neutron", "nnn"),
        ):
            orig = path.read_text()
            try:
                path.write_text(orig.replace(probe, repl, 1))
                f2 = []
                check(f2)
                if not f2:
                    muts += 1
                    print(f"MUTATION NOT REJECTED: {path.name} {probe!r}")
            finally:
                path.write_text(orig)
        print(f"self-test: {4 - muts}/4 mutations rejected")
        if muts:
            sys.exit(1)
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
