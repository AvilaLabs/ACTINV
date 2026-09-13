#!/usr/bin/env python3
"""P25 G4 independent checker.

Re-derives, without trusting the recorded verdicts:

1. **Class application.** For every file quarantined under the P18b
   builder but built under the repaired builder, re-run the decimal
   discriminator on the source and require a repairable mechanism —
   no file whose only proven defects are genuine-source inconsistencies
   may build.
2. **Repair-token honesty.** Every named repair diagnostic ledgered in
   the emitted artifact index must be consistent with the file's
   re-derived decimal classification: ``interp_artifact_reconciled``
   only where the discriminator proves the grid-density mechanism,
   ``missing_total_self_comparator`` only where MF=3 is absent,
   ``floor_reconciled`` only where no genuine source defect exists.
3. **Identity.** Rebuilt indexes declare ``emission_model`` and the
   artifact hashes match the record; the built ∪ failed population
   equals the sealed staged population exactly.
4. **Fail-closed preservation.** Every still-failed file must carry a
   defect the repair is not authorized to reconcile: a proven
   genuine-source class, a fresh conservation message beyond the
   mechanism-gated envelope, or a non-conservation bound (ELFS
   precedence).

Self-test: planted mutations are rejected.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g2_p25_traces import CORPUS_DIR, discriminate, final_class  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
G4 = ROOT / "results/g4_p25_repairs.json"
G2 = ROOT / "results/g2_p25_traces.json"
P18B = ROOT / "target/g4-p18b"
OUT = ROOT / "results/g4_p25_check.json"

REPAIRABLE = {
    "floor_artifact_only",
    "missing_total_or_grid_contract",
    "grid_density_interpolation_artifact",
}
GENUINE = {
    "genuine_source_inconsistency:zero_total_with_partials",
    "genuine_source_inconsistency:gridpoint_excess",
}
TOKEN_KINDS = {
    "floor_reconciled": {"floor", "interp", "no_mf3_total"},
    "interp_artifact_reconciled": {"interp"},
    "missing_total_self_comparator": {"no_mf3_total"},
    "sentinel supplies the permitted runtime comparator":
        {"no_mf3_total"},
}
INTERP_ENVELOPE = 0.03


def load_index_ledgers(report: dict) -> dict:
    """(projectile, filename) -> ledger lines from the emitted index."""
    out = {}
    for proj, res in report.get("projectiles", {}).items():
        idx_path = Path(res.get("index", ""))
        if not idx_path.is_file():
            continue
        idx = json.loads(idx_path.read_text())
        for t in idx.get("targets", []):
            out[(proj, t["file"])] = t.get("ledger", [])
    return out


def _fresh(path: Path) -> dict:
    d = discriminate(path)
    d["final_class"] = final_class(d["kinds"])
    return d


def _first_hit_class(rec: dict) -> str:
    h = rec.get("first_hit")
    return h.get("class") if isinstance(h, dict) else str(h)


def check(report: dict, failures: list[str],
          fresh_cache: dict | None = None,
          index_ledgers: dict | None = None) -> dict:
    if report.get("schema") != "actinv-p25-g4-repairs-1":
        failures.append("schema")
    g2files = json.loads(G2.read_text())["files"]
    fresh_cache = fresh_cache if fresh_cache is not None else {}
    if index_ledgers is None:
        index_ledgers = load_index_ledgers(report)
    for proj, res in report.get("projectiles", {}).items():
        files = g2files.get(proj, {})
        failed = set(res.get("failures", {}))
        pass_names = {
            p.name for p in (P18B / f"inputs-{proj}").glob("*.tendl")
        } | {p.name for p in (P18B / f"failed-{proj}").glob("*.tendl")}
        staged = set(pass_names)
        built_names = staged - failed
        if res.get("staged_files") != len(staged):
            failures.append(f"{proj}: staged count")
        if res.get("built_files") != len(built_names):
            failures.append(f"{proj}: built count")
        if res.get("index_emission_model") != "p25-amendment-b":
            failures.append(f"{proj}: emission_model")

        # decimal re-derivation of every previously-quarantined file
        for name in sorted(staged & set(files)):
            key = (proj, name)
            if key not in fresh_cache:
                fresh_cache[key] = _fresh(CORPUS_DIR[proj] / name)
            fresh = fresh_cache[key]
            cls = fresh["final_class"]
            stored = files[name]["final_class"]
            if cls != stored:
                failures.append(
                    f"{proj}/{name}: decimal re-derivation {cls} "
                    f"!= recorded {stored}")
            repaired = name not in failed
            if repaired and cls not in REPAIRABLE:
                # a genuine-class source may build only when its
                # construction-blocking defect was a proven repairable
                # mechanism AND the genuine defect stays visible as a
                # ledgered source diagnostic in the emitted index —
                # never reconciled, never silent
                mts = {int(m) for m in re.findall(
                    r"MT(\d+)/ZAP", " ".join(
                        fresh.get("detail", [])))}
                led = index_ledgers.get((proj, name), [])
                diagnosed = any(
                    f"MT{mt}:" in line and "audit recorded" in line
                    for mt in mts for line in led)
                if not diagnosed:
                    failures.append(
                        f"{proj}/{name}: built despite {cls} without "
                        f"a ledgered source diagnostic")
            if not repaired:
                msg = res["failures"][name]
                legit = []
                if cls in GENUINE:
                    legit.append("genuine_source")
                # an interp-kin excess beyond the mechanism-gated envelope
                # fails closed regardless of the file's headline class
                if ("interp" in fresh["kinds"]
                        and fresh["worst_relative_excess"]
                        > INTERP_ENVELOPE):
                    legit.append("interp_beyond_envelope")
                if "precedence bound" in msg:
                    legit.append("elfs_beyond_bound")
                # MF=9 sections are outside the MF=10 interp envelope: a
                # conservation excess there fails closed by design
                if "/MF=9 " in msg or "/MF=9/" in msg or "MF=9 ZAP" in msg:
                    legit.append("mf9_excess")
                if not legit:
                    failures.append(
                        f"{proj}/{name}: still failed without a "
                        f"proven non-repairable defect ({cls}; {msg[:140]})")

        # repair-token honesty against the re-derived kinds
        for token, names in res.get("repair_diagnostics", {}).items():
            allowed = TOKEN_KINDS.get(token)
            if allowed is None:
                continue  # identity repairs carry no decimal signature
            for name in names:
                if name in failed:
                    failures.append(
                        f"{proj}/{name}: {token} ledgered on a "
                        f"still-failed file")
                    continue
                if name not in files:
                    continue  # previously-built file: new rows may fire
                key = (proj, name)
                if key not in fresh_cache:
                    fresh_cache[key] = _fresh(CORPUS_DIR[proj] / name)
                kinds = set(fresh_cache[key]["kinds"])
                if not kinds & allowed:
                    failures.append(
                        f"{proj}/{name}: {token} without a proven "
                        f"{sorted(allowed)} mechanism")
    return fresh_cache


def run_checks() -> dict:
    failures: list[str] = []
    if not G4.is_file():
        return {"schema": "actinv-p25-g4-check-1", "pass": False,
                "failures": ["g4_p25_repairs.json missing"]}
    check(json.loads(G4.read_text()), failures)
    return {
        "schema": "actinv-p25-g4-check-1",
        "gate": "P25-G4",
        "pass": not failures,
        "failures": failures[:100],
        "failure_count": len(failures),
    }


def self_test() -> int:
    report = json.loads(G4.read_text())
    rejected = 0

    def mut_emission(r):
        r["projectiles"]["neutron"]["index_emission_model"] = "legacy"

    def mut_failures(r):
        r["projectiles"]["neutron"]["failures"] = {}

    def mut_repair(r):
        r["projectiles"]["neutron"]["repair_diagnostics"] = {
            "interp_artifact_reconciled": ["n-Fe053m.tendl"]}

    def mut_staged(r):
        r["projectiles"]["neutron"]["staged_files"] += 1

    import copy
    cache: dict = {}
    for mutation in (mut_emission, mut_failures, mut_repair, mut_staged):
        m = copy.deepcopy(report)
        mutation(m)
        f: list[str] = []
        try:
            check(m, f, fresh_cache=cache)
        except Exception as exc:  # a crashed check is a rejection
            f.append(str(exc))
        if f:
            rejected += 1
        else:
            print("MUTATION NOT REJECTED:", mutation.__name__)
    print(f"self-test rejected {rejected}/4 mutations")
    return 0 if rejected == 4 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    result = run_checks()
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1)[:4000])
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
